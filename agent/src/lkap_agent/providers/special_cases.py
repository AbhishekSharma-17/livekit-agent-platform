"""Constructor shapes the generic factory kwarg-passthrough cannot handle (PLAN-V2 V2-05).

`ProviderFactory` (`factory.py`) resolves a registry entry's `secret_fields`/
`fields` into a flat `dict[str, Any]` and calls `python_class(**kwargs)`. That
works for the overwhelming majority of the ~120 registry entries, but five
shapes need help, all named explicitly in the V2-05 card:

1. **Classmethod constructors.** `python_class` sometimes names a classmethod,
   not `__init__` (`RealtimeModel.with_azure`, `VAD.load`). Plain
   `importlib.import_module(module).attr` cannot resolve these — the "module"
   segment includes a class. See :func:`import_target`.
2. **Nested avatar configs.** Simli, Anam and Synthesia take one dataclass
   argument instead of flat kwargs (`SimliConfig`, `PersonaConfig`,
   `AvatarConfig`); the registry models this as dotted `FieldSpec.name`s
   (`"simli_config.face_id"`) with `nested_model` set. See
   :func:`unwrap_nested_fields`.
3. **Positional avatar configs.** D-ID's `agent_id` is keyword-only despite
   having no default (nothing special to do — a plain kwarg already works),
   but Synthesia's `avatar_config` is genuinely the first *positional*
   parameter. See :func:`extract_positional_arg`.
4. **`FieldType="file"` fields.** Google STT/TTS `credentials_file`,
   bitHuman's `avatar_image`, LemonSlice's `agent_image` all take a
   filesystem path or an in-memory image, but the resolved config only ever
   carries JSON-safe values over the wire. See :func:`resolve_file_fields`
   for the upload shape this module defines and materializes.
5. **Half-cascade modalities per realtime engine.** Only providers with
   `ProviderCapabilities.text_modality=True` (Gemini, OpenAI/Azure OpenAI
   Realtime, Ultravox) can run `PipelineMode.half_cascade`, and each spells
   "text-only" differently (`modalities=[Modality.TEXT]`, `modalities=["text"]`,
   `output_medium="text"`). See :func:`apply_pipeline_mode`.

`Google`/`AWS` credentials and flat AWS keys are named in the same card
sentence but need **no** code here: the registry already models AWS's flat
`api_key`/`api_secret` (`aws-bedrock-llm`, `aws-polly-tts`) as plain
top-level `FieldSpec`s, so the generic kwarg passthrough already sends them
correctly; Google's ADC credentials are handled entirely by rule 4 above
(`credentials_file` is a `type="file"` field like any other).

**Known scoped-out case**: `aws-transcribe-stt`'s `credentials` field is a
nested `boto3`-style `Credentials` object, not a JSON-safe shape this module
knows how to reconstruct. Until an admin's raw JSON is converted, that field
is passed through unmodified (a `dict`, which `aws.STT` will reject) — in
practice almost every tenant omits it and relies on the standard AWS
credential chain, which is why `requires_credential=False` on that entry.
"""

from __future__ import annotations

import base64
import importlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from lkap_contracts.providers import FieldSpec, ProviderSpec

if TYPE_CHECKING:
    from lkap_contracts.agent_config import PipelineMode

__all__ = [
    "ProviderBuildError",
    "apply_pipeline_mode",
    "extract_positional_arg",
    "import_target",
    "resolve_file_fields",
    "unwrap_nested_fields",
]

#: Provider ids `apply_pipeline_mode` knows an explicit half-cascade override for.
#: Anything else with `text_modality=True` is passed through unmodified (the
#: registry capability is the gate; this table is just "how to say it").
_HALF_CASCADE_MODALITY_KEY: Final[dict[str, str]] = {
    "openai-realtime": "modalities",
    "azure-openai-realtime": "modalities",
}


class ProviderBuildError(RuntimeError):
    """A provider could not be constructed from its resolved configuration."""


def import_target(python_class: str) -> Any:
    """Import a dotted path that may name a class attribute, not just a module.

    Tries the longest possible leading segment as the importable module first
    (the common case, e.g. ``"livekit.plugins.google.realtime.RealtimeModel"``
    → module ``livekit.plugins.google.realtime``, attribute
    ``RealtimeModel``), then backs off one segment at a time so a classmethod
    path such as ``"…openai.realtime.RealtimeModel.with_azure"`` resolves as
    module ``…openai.realtime`` + attribute chain ``RealtimeModel.with_azure``.

    Args:
        python_class: A dotted path, e.g. ``"livekit.plugins.silero.VAD.load"``.

    Returns:
        The imported class, classmethod, or other callable.

    Raises:
        ProviderBuildError: If no leading segment imports, or an attribute in
            the remaining chain does not exist.
    """
    parts = python_class.split(".")
    if len(parts) < 2:
        raise ProviderBuildError(f"malformed python_class: {python_class!r}")

    last_import_error: Exception | None = None
    for split_at in range(len(parts) - 1, 0, -1):
        module_path = ".".join(parts[:split_at])
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            last_import_error = exc
            continue
        obj: Any = module
        attr_chain = parts[split_at:]
        try:
            for attr in attr_chain:
                obj = getattr(obj, attr)
        except AttributeError as exc:
            raise ProviderBuildError(
                f"{module_path!r} has no attribute path {'.'.join(attr_chain)!r}"
            ) from exc
        return obj
    raise ProviderBuildError(
        f"cannot import any module prefix of {python_class!r}: {last_import_error}"
    ) from last_import_error


def _fields_with_nested_model(spec: ProviderSpec) -> list[FieldSpec]:
    return [f for f in (*spec.secret_fields, *spec.fields) if f.nested_model]


def unwrap_nested_fields(spec: ProviderSpec, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build the nested config object(s) a provider's constructor expects.

    Two distinct shapes both use ``FieldSpec.nested_model``, and this handles
    both:

    - **Dotted, grouped** (Simli, Anam): fields named ``"<outer>.<inner>"``,
      e.g. ``simli_config.face_id`` + ``simli_config.api_key``. The api
      resolves these as flat keys because that is what the admin UI's form
      paths look like; this collects every key sharing an ``<outer>``
      prefix, drops the prefix, and constructs
      ``nested_model(**inner_kwargs)``.
    - **Single field, whole value** (Synthesia): one field named
      ``avatar_config`` whose resolved value is already the complete
      ``{"avatar_ids": [...]}`` payload; this constructs
      ``nested_model(**value)`` directly.

    Either way the nested class is imported from the same module as the
    provider's own ``python_class`` — every nested model this registry uses
    is re-exported from that module's package root (verified against the
    1.8.2 source for Simli, Anam and Synthesia).

    Args:
        spec: The registry entry being built.
        kwargs: The resolved kwargs, possibly containing dotted keys and/or a
            plain key whose value needs wrapping.

    Returns:
        A new kwargs dict with every nested group/value replaced by one
        ``<name>: <nested instance>`` entry; everything else passes through
        unchanged.

    Raises:
        ProviderBuildError: If a nested model class cannot be imported.
    """
    nested_fields = _fields_with_nested_model(spec)
    if not nested_fields:
        return kwargs

    dotted_outer_to_model: dict[str, str] = {}
    plain_name_to_model: dict[str, str] = {}
    for field in nested_fields:
        model_name = field.nested_model
        assert model_name is not None  # noqa: S101  (guarded by the filter above)
        if "." in field.name:
            outer, _, _inner = field.name.partition(".")
            dotted_outer_to_model[outer] = model_name
        else:
            plain_name_to_model[field.name] = model_name

    grouped: dict[str, dict[str, Any]] = {}
    passthrough: dict[str, Any] = {}
    for key, value in kwargs.items():
        outer, sep, inner = key.partition(".")
        if sep and outer in dotted_outer_to_model:
            grouped.setdefault(outer, {})[inner] = value
        else:
            passthrough[key] = value

    module_path = spec.python_class.rsplit(".", 1)[0]
    for outer, inner_kwargs in grouped.items():
        model_cls = import_target(f"{module_path}.{dotted_outer_to_model[outer]}")
        passthrough[outer] = model_cls(**inner_kwargs)

    for name, model_name in plain_name_to_model.items():
        value = passthrough.get(name)
        if isinstance(value, dict):
            model_cls = import_target(f"{module_path}.{model_name}")
            passthrough[name] = model_cls(**value)

    return passthrough


def extract_positional_arg(spec: ProviderSpec, kwargs: dict[str, Any]) -> tuple[Any | None, dict[str, Any]]:
    """Pull out the one field the plugin takes positionally (Synthesia's ``avatar_config``).

    Args:
        spec: The registry entry being built.
        kwargs: The resolved kwargs, after :func:`unwrap_nested_fields`.

    Returns:
        ``(positional_value, remaining_kwargs)``; ``positional_value`` is
        ``None`` when the spec declares no ``positional=True`` field (the
        overwhelmingly common case).
    """
    positional_field = next((f for f in (*spec.secret_fields, *spec.fields) if f.positional), None)
    if positional_field is None:
        return None, kwargs
    outer = positional_field.name.split(".", 1)[0]
    remaining = dict(kwargs)
    value = remaining.pop(outer, None)
    return value, remaining


def _file_field_names(spec: ProviderSpec) -> set[str]:
    return {f.name for f in (*spec.secret_fields, *spec.fields) if f.type == "file"}


def _write_temp_file(upload: dict[str, Any]) -> str:
    """Materialize an uploaded file value to a per-build temp path.

    Args:
        upload: ``{"name": <original filename>, "content_base64": <data>}`` —
            the shape the api sends for any ``FieldType="file"`` field (a
            decision this package made explicitly: CONTRACTS-V2 does not
            define one, and this is the only place a file field is consumed).

    Returns:
        The path the plugin's constructor kwarg should receive.
    """
    suffix = Path(str(upload.get("name", ""))).suffix
    data = base64.b64decode(upload["content_base64"])
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="lkap-provider-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def resolve_file_fields(spec: ProviderSpec, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Replace ``{"name", "content_base64"}`` uploads with a filesystem path.

    Covers Google STT/TTS's ``credentials_file`` (a service-account JSON key,
    since those classes have no ``api_key`` kwarg at all — see the registry's
    ``google-stt``/``google-tts`` notes) and, if configured with an uploaded
    image rather than a bare id/URL, bitHuman's ``avatar_image`` and
    LemonSlice's ``agent_image``.

    Args:
        spec: The registry entry being built.
        kwargs: The resolved kwargs, after :func:`unwrap_nested_fields`.

    Returns:
        A new kwargs dict; keys that are not file fields, or whose value is
        not an upload dict (e.g. an admin pasted a bare path instead), pass
        through unchanged.
    """
    file_fields = _file_field_names(spec)
    if not file_fields:
        return kwargs
    result = dict(kwargs)
    for name in file_fields:
        value = result.get(name)
        if isinstance(value, dict) and "content_base64" in value:
            result[name] = _write_temp_file(value)
    return result


def apply_pipeline_mode(spec: ProviderSpec, kwargs: dict[str, Any], mode: PipelineMode) -> dict[str, Any]:
    """Select audio-native vs. text-only modalities for a realtime provider.

    Only called for ``kind="realtime"`` slots. Cascaded/realtime modes leave
    ``kwargs`` untouched (each engine's own default already fits: audio-native
    realtime models default to audio, and cascaded mode never builds a
    realtime provider at all). ``half_cascade`` requires
    ``ProviderCapabilities.text_modality`` and fails loudly otherwise, since a
    silently audio-native model in half-cascade mode would double-speak
    alongside the paired TTS.

    Args:
        spec: The realtime provider's registry entry.
        kwargs: The resolved kwargs, after the other special cases.
        mode: The agent's configured pipeline mode.

    Returns:
        ``kwargs``, adjusted for ``half_cascade`` where applicable.

    Raises:
        ProviderBuildError: ``mode == "half_cascade"`` but the provider has no
            text-only path (``text_modality=False``).
    """
    if mode != "half_cascade":
        return kwargs
    if not spec.capabilities.text_modality:
        raise ProviderBuildError(f"{spec.id} has no text_modality; it cannot run in half_cascade mode")
    result = dict(kwargs)
    if spec.id == "ultravox-realtime":
        result["output_medium"] = "text"
    elif spec.id in _HALF_CASCADE_MODALITY_KEY:
        result[_HALF_CASCADE_MODALITY_KEY[spec.id]] = ["text"]
    # google-realtime's TEXT-vs-AUDIO modality is chosen in
    # `_google_realtime_kwargs` (factory.py), which already needs the SDK's
    # `google.genai.types.Modality` enum and is mode-aware there directly.
    return result
