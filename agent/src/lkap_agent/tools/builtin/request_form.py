"""`request_form` built-in tool (CONTRACTS-V2 §4.4): ask the user to fill in a form.

The model describes the fields; the tool turns them into a JSON schema
(`fields_to_schema`) and calls `UiChannel.request_form` on a `form` block.

* **cascaded**: blocking. The tool returns the submitted values (or a "not
  submitted" line after cancel / timeout / session close) as its result.
* **realtime / half_cascade**: returns `None` at once and the wait runs on
  the session's `BackgroundRunner`; a submission arrives as an *urgent*
  background result (the model is prompted to acknowledge it), a missed form
  as a routine note. `PlatformAgent` also cancels the tool's own reply in
  these modes (D-W2-9i), so the model does not talk over the form.
* **text channel** (any mode; asks #30 / B-5): a typed chat has no form panel
  it can submit, so the tool shows nothing and answers at once, telling the
  model to ask for the fields in the conversation. Waiting would block the
  turn until the form timed out.

V5-19 adds the `phone`, `textarea` and `file` field types (the shared
`lkap_contracts.ui_protocol.FormFieldType`). A `file` field goes through the
upload path: the browser streams each file on `lkap.ui.upload` with the form's
`block_id` and the field's name, the channel checks it against the field's
`x-lkap-upload` limits and stores it, and the submitted value keeps only asset
ids the session really stored for that field.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.agent_config import PipelineMode
from lkap_contracts.blocks import validate_accept
from lkap_contracts.ui_protocol import (
    FORM_UPLOAD_KEY,
    FORM_WIDGET_KEY,
    MAX_UPLOAD_FILES,
    FormFieldType,
    FormUploadSpec,
)
from packs.base import PackSessionContext
from pydantic import BaseModel, Field

from lkap_agent.tools.json_args import json_list
from lkap_agent.ui.blocks import describe_blocks, pick_block, session_block_specs

__all__ = [
    "BACKGROUND_FORM_MODES",
    "FORM_TIMEOUT_S",
    "FieldType",
    "FormField",
    "build_request_form_tool",
    "fields_to_schema",
    "text_channel_form_note",
]

#: How long the user has to submit (CONTRACTS-V2 §4.4 default).
FORM_TIMEOUT_S: Final[float] = 120.0

#: Pipelines whose model is a `RealtimeModel`: the form wait runs in the background.
BACKGROUND_FORM_MODES: Final[frozenset[PipelineMode]] = frozenset({"realtime", "half_cascade"})

#: The field types a form offers (`lkap_contracts.ui_protocol.FormFieldType`).
FieldType = FormFieldType


class FormField(BaseModel):
    """One field of a form the model asks the user to fill in."""

    name: str = Field(description="Machine name of the field, e.g. policy_number.")
    label: str = Field(description="What the user sees, e.g. Policy number.")
    type: FieldType = Field(default="string", description="The kind of value.")
    required: bool = Field(default=False, description="Whether the user must fill it in.")
    options: list[str] = Field(default_factory=list, description="The choices, for type select only.")
    accept: list[str] = Field(
        default_factory=list,
        description="For type file only: image/* or application/pdf (empty: photos and PDFs).",
    )
    max_files: int = Field(default=1, ge=1, le=MAX_UPLOAD_FILES, description="For type file only.")


#: JSON text of the fields is read too (V6-30, F-2).
FormFieldList = Annotated[
    list[FormField], json_list('{"name": "policy_number", "label": "Policy number", "required": true}')
]


def fields_to_schema(fields: list[FormField]) -> dict[str, Any]:
    """The JSON schema (draft 2020-12 subset) a `form` block renders.

    Mapping: `string`/`number`/`integer`/`boolean` → the same JSON-schema
    `type`; `date` / `email` / `phone` → `{type: string, format: <type>}`;
    `select` → `{type: string, enum: options}`; `textarea` → `{type: string,
    "x-lkap-widget": "textarea"}`; `file` → `{type: array, items: {type:
    string}, maxItems, "x-lkap-widget": "file", "x-lkap-upload": {accept,
    max_files, max_bytes}}` (the value is the stored asset ids). `label` →
    `title`; `required: true` → listed in `required`. Property order follows
    `fields`.
    """
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for field in fields:
        prop: dict[str, Any] = {"title": field.label}
        match field.type:
            case "date" | "email" | "phone":
                prop.update(type="string", format=field.type)
            case "select":
                prop.update(type="string", enum=list(field.options))
            case "textarea":
                prop.update({"type": "string", FORM_WIDGET_KEY: "textarea"})
            case "file":
                spec = FormUploadSpec(accept=validate_accept(field.accept), max_files=field.max_files)
                prop.update(
                    {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": spec.max_files,
                        FORM_WIDGET_KEY: "file",
                        FORM_UPLOAD_KEY: spec.model_dump(mode="json"),
                    }
                )
            case _:
                prop["type"] = field.type
        properties[field.name] = prop
        if field.required:
            required.append(field.name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def text_channel_form_note(fields: list[FormField]) -> str:
    """The tool result on the text channel: no form is shown, ask in the conversation."""
    labels = ", ".join(f"{f.label}{' (required)' if f.required else ''}" for f in fields if f.type != "file")
    note = (
        "No form was shown: this is a text chat, and forms cannot be filled in here. "
        "The answers will come by text in this conversation. "
        f"Ask the user for these, one or a few at a time: {labels}."
    )
    if any(f.type == "file" for f in fields):
        note += " Files can't be sent in this chat; continue without them."
    return note


def _validate_fields(fields: list[FormField]) -> None:
    if not fields:
        raise ToolError("Pass at least one field.")
    names = [f.name for f in fields]
    if len(set(names)) != len(names) or not all(names):
        raise ToolError("Field names must be unique and non-empty.")
    for field in fields:
        if field.type == "select" and not field.options:
            raise ToolError(f"Field {field.name!r} is a select; give it options.")
        if field.type == "file":
            try:
                validate_accept(field.accept)
            except ValueError as exc:
                raise ToolError(f"Field {field.name!r}: {exc}.") from exc


def build_request_form_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `request_form` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["form"])

    async def request_form(context: RunContext[Any], block_id: str, fields: FormFieldList) -> str | None:
        """Show the user a form in their side panel and wait for them to submit it.

        Args:
            block_id: The form block to use.
            fields: The fields to ask for, in order.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "form", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        _validate_fields(fields)
        if getattr(ctx, "channel", "web") == "text":
            ctx.log.debug(
                "builtin_tool.request_form",
                call_id=context.function_call.call_id,
                block_id=target,
                fields=[f.name for f in fields],
                text_channel=True,
            )
            return text_channel_form_note(fields)
        schema = fields_to_schema(fields)
        ctx.log.debug(
            "builtin_tool.request_form",
            call_id=context.function_call.call_id,
            block_id=target,
            fields=[f.name for f in fields],
            background=ctx.pipeline_mode in BACKGROUND_FORM_MODES,
        )

        if ctx.pipeline_mode in BACKGROUND_FORM_MODES:
            ctx.background.submit(
                name="request_form",
                coro=ctx.ui.request_form(target, schema, timeout_s=FORM_TIMEOUT_S),
                urgent=lambda values: values is not None,
                urgent_instructions=lambda values: (
                    f"The user just submitted the {target} form with these values: "
                    f"{json.dumps(values)}. Acknowledge them briefly and continue."
                ),
                routine_note=lambda values: (
                    None if values is not None else f"The user did not submit the {target} form."
                ),
                call_id=context.function_call.call_id,
            )
            return None

        values = await ctx.ui.request_form(target, schema, timeout_s=FORM_TIMEOUT_S)
        if values is None:
            return "The user did not submit the form (dismissed or timed out)."
        return json.dumps({"submitted": True, "values": values})

    return function_tool(
        request_form,
        description=(
            "Show the user a form in their side panel and wait for them to submit it. "
            "Tell the user the form is on their screen. "
            f"Form blocks: {inventory}."
        ),
    )
