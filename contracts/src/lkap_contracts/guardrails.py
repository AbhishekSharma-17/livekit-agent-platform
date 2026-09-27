"""Guardrails (V5-39, P §4.2 C24): rules on what the caller says, what the agent says and what tools return.

Audio cannot be filtered before it is heard, so every check runs on text: the
caller's finished turn (``input``), the agent's reply as it streams, one sentence
at a time (``output``), and a tool's result before the model reads it
(``tool_output``). A rule is one of three kinds:

* :class:`RegexRule` — a pattern, checked in the worker in microseconds; it
  cannot time out. A pattern that does not compile is refused when the agent is
  saved.
* :class:`ClassifierRule` — a plain-language instruction a language model
  judges the text against (``GuardrailsConfig.model``, else the agent's workflow
  model). Bounded by ``budget_ms``; on a timeout or an error it **fails open**
  (the text goes through) and a ``guardrail_timeout`` event is recorded.
* :class:`ProviderRule` — a hosted moderation service (``openai_moderation``:
  OpenAI's moderation endpoint with ``omni-moderation-latest``). Same budget and
  the same fail-open rule.

On a trip (``on_trip``): ``interrupt`` stops the agent and speaks ``safe_reply``;
``end_call`` also ends the call once the reply is spoken; ``escalate`` also calls
``escalate_to_human``. A ``tool_output`` trip always replaces the tool's result
with ``safe_reply`` (action ``replaced``). Every trip records a ``guardrail``
session event (:class:`GuardrailEvent`) and an activity row
(``ActivityEvent.kind == "guardrail"``).

An agent without rules runs exactly as before: no check, no model call, no added
latency.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from lkap_contracts.common import ProviderRef

__all__ = [
    "DEFAULT_GUARDRAIL_BUDGET_MS",
    "DEFAULT_SAFE_REPLY",
    "GUARDRAIL_EVENT",
    "GUARDRAIL_TIMEOUT_EVENT",
    "MAX_CLASSIFIER_PROMPT_CHARS",
    "MAX_GUARDRAIL_EXCERPT_CHARS",
    "MAX_GUARDRAIL_RULES",
    "MAX_PATTERN_CHARS",
    "MAX_RULE_NAME_CHARS",
    "MAX_SAFE_REPLY_CHARS",
    "MODERATION_CATEGORIES",
    "ClassifierRule",
    "GuardrailAction",
    "GuardrailEvent",
    "GuardrailFailure",
    "GuardrailOnTrip",
    "GuardrailRule",
    "GuardrailRuleKind",
    "GuardrailStage",
    "GuardrailTimeoutEvent",
    "GuardrailsConfig",
    "ModerationCategory",
    "ModerationProvider",
    "ProviderRule",
    "RegexRule",
]

#: The session event of a trip.
GUARDRAIL_EVENT: Final[str] = "guardrail"
#: The session event of a check that did not answer in time (or failed) and so let the text through.
GUARDRAIL_TIMEOUT_EVENT: Final[str] = "guardrail_timeout"

#: Most rules in one of the three lists.
MAX_GUARDRAIL_RULES: Final[int] = 20
#: Longest rule name (it names the rule on the timeline and the activity feed).
MAX_RULE_NAME_CHARS: Final[int] = 60
#: Longest regex pattern.
MAX_PATTERN_CHARS: Final[int] = 300
#: Longest classifier instruction.
MAX_CLASSIFIER_PROMPT_CHARS: Final[int] = 1000
#: Longest safe reply.
MAX_SAFE_REPLY_CHARS: Final[int] = 500
#: Longest excerpt a ``guardrail`` event keeps (only on ``privacy.storage_tier == "full"``).
MAX_GUARDRAIL_EXCERPT_CHARS: Final[int] = 120
#: The default time a classifier or moderation check may take before the text goes through.
DEFAULT_GUARDRAIL_BUDGET_MS: Final[int] = 300

#: What the agent says after a trip unless the agent sets its own line.
DEFAULT_SAFE_REPLY: Final[str] = (
    "I'm sorry, I can't help with that. Is there anything else I can help you with?"
)

#: Where a rule applies: the caller's turn, the agent's reply, or a tool's result.
GuardrailStage = Literal["input", "output", "tool_output"]
#: What a trip on ``input`` or ``output`` does.
GuardrailOnTrip = Literal["interrupt", "end_call", "escalate"]
#: What a trip did: ``on_trip`` for input and output, ``replaced`` for a tool's result.
GuardrailAction = Literal["interrupt", "end_call", "escalate", "replaced"]
#: The three rule kinds.
GuardrailRuleKind = Literal["regex", "classifier", "provider"]
#: Why a check let the text through: it ran past ``budget_ms``, it failed, or it could not run.
GuardrailFailure = Literal["timeout", "error", "unavailable"]
#: The hosted moderation services a :class:`ProviderRule` can use.
ModerationProvider = Literal["openai_moderation"]

#: OpenAI moderation categories (``omni-moderation-latest``), as the service names them.
ModerationCategory = Literal[
    "harassment",
    "harassment/threatening",
    "hate",
    "hate/threatening",
    "illicit",
    "illicit/violent",
    "self-harm",
    "self-harm/intent",
    "self-harm/instructions",
    "sexual",
    "sexual/minors",
    "violence",
    "violence/graphic",
]
MODERATION_CATEGORIES: Final[tuple[str, ...]] = get_args(ModerationCategory)


class _Rule(BaseModel):
    """What every rule has: a name, unique within its list, shown on the timeline and the feed."""

    name: str = Field(min_length=1, max_length=MAX_RULE_NAME_CHARS, description="What the rule is called.")

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("the rule needs a name")
        return cleaned


class RegexRule(_Rule):
    """A pattern (Python regular expression syntax). The text trips the rule when it matches anywhere."""

    kind: Literal["regex"] = "regex"
    pattern: str = Field(
        min_length=1,
        max_length=MAX_PATTERN_CHARS,
        description="A regular expression, e.g. `\\b(?:\\d[ -]?){13,19}\\b` for card numbers. A pattern "
        "that does not compile, or that repeats a repeated group (slow on long text), is refused on save.",
    )
    ignore_case: bool = Field(default=True, description="Match upper and lower case alike.")


class ClassifierRule(_Rule):
    """A plain-language rule a language model judges the text against."""

    kind: Literal["classifier"] = "classifier"
    prompt: str = Field(
        min_length=1,
        max_length=MAX_CLASSIFIER_PROMPT_CHARS,
        description="What the text must not do, in plain words, e.g. `Gives medical advice: tells the "
        "caller what medicine or dose to take, or what their symptoms mean.`",
    )


class ProviderRule(_Rule):
    """A hosted moderation service. The text trips the rule when the service flags it."""

    kind: Literal["provider"] = "provider"
    provider: ModerationProvider = Field(
        default="openai_moderation",
        description="`openai_moderation`: OpenAI's moderation service (free with an OpenAI key).",
    )
    categories: list[ModerationCategory] = Field(
        default=[],
        description="Only these categories trip the rule; empty: any category the service flags.",
    )
    credential_id: str | None = Field(
        default=None,
        description="The OpenAI key to use; `None` uses the agent's own OpenAI language-model key.",
    )

    @field_validator("categories")
    @classmethod
    def _dedupe(cls, values: list[ModerationCategory]) -> list[ModerationCategory]:
        return list(dict.fromkeys(values))


#: One rule of any kind (``kind`` tells them apart).
GuardrailRule = Annotated[RegexRule | ClassifierRule | ProviderRule, Field(discriminator="kind")]

_Rules = Annotated[list[GuardrailRule], Field(max_length=MAX_GUARDRAIL_RULES)]


class GuardrailsConfig(BaseModel):
    """What the agent checks, and what it does when a check trips (V5-39). Empty by default: no checks."""

    input: _Rules = Field(default=[], description="Rules on what the caller says (each finished turn).")
    output: _Rules = Field(
        default=[], description="Rules on what the agent says, checked one sentence at a time as it speaks."
    )
    tool_output: _Rules = Field(
        default=[], description="Rules on what a tool returns, checked before the agent reads it."
    )
    on_trip: GuardrailOnTrip = Field(
        default="interrupt",
        description="What a trip on what the caller or the agent says does: `interrupt` stops the agent "
        "and says the safe reply; `end_call` also ends the call; `escalate` also asks a person to take "
        "over (`escalate_to_human`). A tool's result that trips is always replaced by the safe reply.",
    )
    safe_reply: str = Field(
        default=DEFAULT_SAFE_REPLY,
        min_length=1,
        max_length=MAX_SAFE_REPLY_CHARS,
        description="What the agent says after a trip, word for word.",
    )
    model: ProviderRef | None = Field(
        default=None,
        description="The language model that judges classifier rules (a small, fast one is best); "
        "`None` uses the agent's workflow model.",
    )
    budget_ms: int = Field(
        default=DEFAULT_GUARDRAIL_BUDGET_MS,
        ge=50,
        le=2000,
        description="How long a classifier or moderation check may take; past it the text goes through "
        "and a `guardrail_timeout` event is recorded.",
    )

    @field_validator("safe_reply")
    @classmethod
    def _clean_reply(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("the safe reply is empty")
        return cleaned

    @model_validator(mode="after")
    def _unique_names(self) -> GuardrailsConfig:
        for stage in ("input", "output", "tool_output"):
            seen: set[str] = set()
            for rule in getattr(self, stage):
                key = rule.name.casefold()
                if key in seen:
                    raise ValueError(f"two {stage} rules are called '{rule.name}'")
                seen.add(key)
        return self

    def rules(self, stage: GuardrailStage) -> list[RegexRule | ClassifierRule | ProviderRule]:
        """The rules of one stage."""
        rules: list[RegexRule | ClassifierRule | ProviderRule] = getattr(self, stage)
        return rules

    @property
    def active(self) -> bool:
        """Whether any rule is set (an agent without rules runs no check at all)."""
        return bool(self.input or self.output or self.tool_output)


class GuardrailEvent(BaseModel):
    """Payload of the ``guardrail`` session event: a rule tripped.

    ``excerpt_hash`` is a keyed hash of the text that tripped (the key is random per
    session and never stored): two trips on the same text in one session carry the
    same hash, but the text cannot be recovered or matched across sessions.
    ``excerpt`` (at most :data:`MAX_GUARDRAIL_EXCERPT_CHARS`) is kept only when the
    agent's ``privacy.storage_tier`` is ``full``.
    """

    stage: GuardrailStage
    rule: str
    kind: GuardrailRuleKind
    action: GuardrailAction
    excerpt_hash: str
    excerpt: str | None = None
    categories: list[str] = Field(
        default=[], description="What the moderation service flagged (provider rules)."
    )
    tool: str | None = Field(default=None, description="The tool whose result tripped (`tool_output`).")
    latency_ms: int | None = Field(default=None, ge=0, description="How long the check that tripped took.")


class GuardrailTimeoutEvent(BaseModel):
    """Payload of the ``guardrail_timeout`` session event: a check let the text through (fail open)."""

    stage: GuardrailStage
    rule: str
    kind: GuardrailRuleKind
    reason: GuardrailFailure
    budget_ms: int = Field(ge=0)
