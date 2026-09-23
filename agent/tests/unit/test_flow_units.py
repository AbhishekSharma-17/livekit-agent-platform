"""V2-15 flow runtime units: variables, provider overrides, config prep, KB scope, SDK tripwires."""

from __future__ import annotations

from typing import Any

import pytest
from fakes.fake_api import resolved_config
from fakes.fake_ctx import FakeKbClient, FakeStructuredLLM
from livekit.agents import Agent, AgentSession, ChatContext, llm
from livekit.agents.voice.generation import make_tool_output
from lkap_contracts.agent_config import ProviderRef, ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.api_models import KbHit
from lkap_contracts.flow import AgentNode, FlowEdge, FlowSpec, FlowState, VariableSpec

from lkap_agent.flow import ScopedKbClient, prepare_flow_resolved
from lkap_agent.flow.edges import edge_description, group_edges_by_target
from lkap_agent.flow.providers import NodeProviders, resolve_override
from lkap_agent.flow.state import FlowUserdata, attach_flow_state
from lkap_agent.flow.variables import (
    extract_variables,
    known_variables_block,
    referenced_variables,
    render_template,
    transcript_text,
    variables_model,
)
from lkap_agent.providers.factory import ProviderFactory

# ------------------------------------------------------------------ variables


def test_render_template_fills_known_and_blanks_missing() -> None:
    text = "Hi {{ name }}, age {{age}}, member {{ member }}, x {{ unknown }}."
    rendered = render_template(text, {"name": "Ada", "age": 36, "member": True}, missing="?")
    assert rendered == "Hi Ada, age 36, member yes, x ?."
    assert referenced_variables(text) == {"name", "age", "member", "unknown"}


def test_known_variables_block_lists_only_set_values() -> None:
    specs = [VariableSpec(name="name", description="full name"), VariableSpec(name="policy")]
    block = known_variables_block({"name": "Ada", "policy": None}, specs)
    assert block is not None
    assert "- name (full name): Ada" in block
    assert "policy" not in block
    assert known_variables_block({}, specs) is None


def test_variables_model_is_all_optional_with_enum_hint() -> None:
    model = variables_model(
        [
            VariableSpec(name="name"),
            VariableSpec(name="count", type="number"),
            VariableSpec(name="ok", type="boolean"),
            VariableSpec(name="kind", type="enum", options=["auto", "home"]),
        ]
    )
    assert model().model_dump() == {"name": None, "count": None, "ok": None, "kind": None}
    schema = model.model_json_schema()
    assert schema["properties"]["kind"]["enum"] == ["auto", "home", None]


async def test_extract_variables_coerces_and_drops_invalid_values() -> None:
    specs = [
        VariableSpec(name="name"),
        VariableSpec(name="count", type="number"),
        VariableSpec(name="kind", type="enum", options=["auto", "home"]),
        VariableSpec(name="ok", type="boolean"),
    ]
    schema = variables_model(specs)
    structured = FakeStructuredLLM([schema(name=" Ada ", count=3.0, kind="HOME", ok=None)])
    values = await extract_variables(structured, specs, "user: hi", timeout_s=5)
    assert values == {"name": "Ada", "count": 3, "kind": "home"}
    instructions, transcript, used_schema = structured.calls[0]
    assert "kind must be one of: auto, home" in instructions
    assert transcript == "user: hi"
    assert used_schema is schema or used_schema.model_fields.keys() == schema.model_fields.keys()


async def test_extract_variables_never_raises() -> None:
    class _Broken:
        async def extract(self, **_: Any) -> Any:
            raise RuntimeError("model down")

    assert await extract_variables(_Broken(), [VariableSpec(name="x")], "user: hi", timeout_s=1) == {}
    assert await extract_variables(_Broken(), [], "user: hi", timeout_s=1) == {}


def test_transcript_text_keeps_user_and_assistant_text() -> None:
    chat = ChatContext.empty()
    chat.add_message(role="system", content="secret instructions")
    chat.add_message(role="user", content="hello")
    chat.add_message(role="assistant", content="hi there")
    assert transcript_text(chat) == "user: hello\nassistant: hi there"


# ------------------------------------------------------------------- edges


def test_same_target_edges_merge_into_one_description() -> None:
    edges = [
        FlowEdge(id="a", source="s", target="t", condition="Caller is angry."),
        FlowEdge(id="b", source="s", target="t", condition="Caller asks for a human."),
        FlowEdge(id="c", source="s", target="u", condition=""),
    ]
    groups = group_edges_by_target(edges)
    assert list(groups) == ["t", "u"]
    assert edge_description(groups["t"], "T") == (
        "Call this when any of these is true: (Caller is angry.) OR (Caller asks for a human.)"
    )
    assert edge_description(groups["u"], "Wrap up") == "Move the conversation to the next step: Wrap up."


# -------------------------------------------------------- provider overrides


class _RecordingFactory(ProviderFactory):
    def __init__(self) -> None:
        self.built: list[tuple[str, ResolvedProvider]] = []

    def build(self, slot: Any, provider: ResolvedProvider, *, mode: Any = "cascaded") -> Any:
        self.built.append((slot, provider))
        return f"built:{slot}:{provider.model}"


def _node(**providers: ProviderRef) -> AgentNode:
    return AgentNode(id="n", instructions="x", providers=providers)  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
def test_realtime_modes_ignore_provider_overrides_with_a_warning(mode: str) -> None:
    factory = _RecordingFactory()
    providers = NodeProviders(factory=factory, resolved=resolved_config(mode=mode), mode=mode)  # type: ignore[arg-type]
    kwargs = providers.agent_kwargs(_node(llm=ProviderRef(provider_id="livekit-inference-llm", model="m")))
    assert kwargs == {}
    assert factory.built == []
    assert providers.warnings and "ignored in" in providers.warnings[0]


def test_cascaded_override_on_the_sessions_provider_reuses_its_resolved_kwargs() -> None:
    resolved = resolved_config()
    factory = _RecordingFactory()
    providers = NodeProviders(factory=factory, resolved=resolved, mode="cascaded")
    ref = ProviderRef(
        provider_id="livekit-inference-llm", model="openai/gpt-5-mini", fields={"temperature": 0.1}
    )
    assert providers.agent_kwargs(_node(llm=ref)) == {"llm": "built:llm:openai/gpt-5-mini"}
    slot, built = factory.built[0]
    assert slot == "llm"
    assert built.kwargs["temperature"] == 0.1
    assert providers.warnings == []


def test_credential_less_provider_is_resolved_from_the_registry() -> None:
    resolved = resolved_config(with_tts=False)
    provider, reason = resolve_override(
        "tts", ProviderRef(provider_id="livekit-inference-tts", fields={"voice": "Ashley"}), resolved
    )
    assert reason is None and provider is not None
    assert provider.python_class.endswith("inference.TTS")
    assert provider.kwargs["voice"] == "Ashley"


def test_credentialed_provider_the_api_did_not_resolve_is_skipped() -> None:
    resolved = resolved_config()
    provider, reason = resolve_override(
        "llm", ProviderRef(provider_id="openai-llm", credential_id="cred-x", model="gpt-5"), resolved
    )
    assert provider is None
    assert reason is not None and "credential" in reason
    providers = NodeProviders(factory=_RecordingFactory(), resolved=resolved, mode="cascaded")
    assert providers.agent_kwargs(_node(llm=ProviderRef(provider_id="openai-llm", credential_id="c"))) == {}
    assert providers.warnings


def test_override_with_the_wrong_kind_is_rejected() -> None:
    provider, reason = resolve_override(
        "llm", ProviderRef(provider_id="livekit-inference-tts"), resolved_config()
    )
    assert provider is None and reason is not None and "not 'llm'" in reason


# ------------------------------------------------------------------ prepare


def _with_flow(resolved: ResolvedAgentConfig, flow: dict[str, Any]) -> ResolvedAgentConfig:
    config = resolved.config.model_copy(update={"flow": FlowSpec.model_validate(flow)})
    return resolved.model_copy(update={"config": config})


_QA_FLOW = {
    "nodes": [
        {"id": "start", "kind": "start", "greeting": "Welcome!", "greeting_mode": "generate"},
        {"id": "qa", "kind": "qa", "rubric_prompt": "Score empathy."},
    ],
    "edges": [],
}


def test_prepare_flow_resolved_applies_start_greeting_and_qa_node() -> None:
    base = resolved_config(greeting="Old")
    base = base.model_copy(
        update={
            "resolved": {
                **base.resolved,
                "qa_llm": ResolvedProvider(
                    provider_id="livekit-inference-llm",
                    python_class="livekit.agents.inference.LLM",
                    model="m",
                    kwargs={},
                ),
            }
        }
    )
    prepared = prepare_flow_resolved(_with_flow(base, _QA_FLOW))
    assert prepared.config.voice.greeting == "Welcome!"
    assert prepared.config.voice.greeting_mode == "generate"
    assert prepared.config.qa.enabled is True
    assert prepared.config.qa.rubric_prompt == "Score empathy."


def test_prepare_flow_resolved_turns_qa_on_for_a_qa_node_even_without_a_resolved_judge() -> None:
    """R-V2-11: the node is the author's intent; a missing `qa_llm` is the judge's
    `failed, "qa_llm not resolved"` (R-V2-6), never QA silently staying off."""
    prepared = prepare_flow_resolved(_with_flow(resolved_config(), _QA_FLOW))
    assert prepared.config.qa.enabled is True
    assert prepared.config.qa.rubric_prompt == "Score empathy."


def test_prepare_flow_resolved_leaves_prompt_agents_alone() -> None:
    resolved = resolved_config()
    assert prepare_flow_resolved(resolved) is resolved
    empty = _with_flow(resolved, {"nodes": [], "edges": []})
    assert prepare_flow_resolved(empty) is empty


# --------------------------------------------------------------- state / kb


class _Session:
    def __init__(self) -> None:
        self._userdata: Any = None

    @property
    def userdata(self) -> Any:
        if self._userdata is None:
            raise ValueError("AgentSession userdata is not set")
        return self._userdata

    @userdata.setter
    def userdata(self, value: Any) -> None:
        self._userdata = value


def test_attach_flow_state_sets_session_userdata_flow() -> None:
    state = FlowState(current_node="start", path=["start"])
    session = _Session()
    attach_flow_state(session, state)
    assert isinstance(session.userdata, FlowUserdata)
    assert session.userdata.flow is state
    as_dict: dict[str, Any] = {}
    session.userdata = as_dict
    attach_flow_state(session, state)
    assert as_dict["flow"] is state


async def test_scoped_kb_client_follows_the_node_scope() -> None:
    inner = FakeKbClient([KbHit(chunk_id="c", document_id="d", filename="f", score=1.0, text="t")])
    scoped = ScopedKbClient(inner, [])
    assert await scoped.search("q") == []
    assert inner.queries == []
    scoped.set_scope(["kb1"])
    assert len(await scoped.search("q", k=2)) == 1
    assert inner.queries[-1] == ("q", 2, ["kb1"])
    await scoped.search("q", kb_ids=["kb2"])
    assert inner.queries[-1] == ("q", 4, ["kb2"])


# ---------------------------------------------------------------- tripwires


async def test_sdk_tripwire_a_bare_agent_return_is_a_silent_handoff() -> None:
    """livekit-agents 1.8.2: a tool returning an `Agent` hands off and asks for no reply."""
    agent = Agent(instructions="next")
    call = llm.FunctionCall(call_id="c1", name="go_to_next", arguments="{}")
    out = make_tool_output(fnc_call=call, output=agent, exception=None)
    assert out.agent_task is agent
    assert out.fnc_call_out.reply_required is False
    with_text = make_tool_output(fnc_call=call, output=(agent, "moving on"), exception=None)
    assert with_text.agent_task is agent
    assert with_text.fnc_call_out.reply_required is True


def test_sdk_tripwire_agent_copies_chat_ctx_and_drops_foreign_tool_calls() -> None:
    """`Agent(chat_ctx=...)` copies with `tools=` filtering: a carried history keeps its
    messages but loses function-call pairs for tools the new node does not have. `id=`
    is honoured for subclasses only (a plain `Agent` is always `default_agent`)."""

    class _Node(Agent):
        pass

    chat = ChatContext.empty()
    chat.add_message(role="user", content="my name is Ada")
    chat.insert(llm.FunctionCall(call_id="c1", name="go_to_b", arguments="{}"))
    chat.insert(llm.FunctionCallOutput(call_id="c1", name="go_to_b", output="", is_error=False))
    agent = _Node(instructions="b", chat_ctx=chat, id="b")
    assert agent.id == "b"
    assert agent.chat_ctx is not chat
    assert [i.type for i in agent.chat_ctx.items] == ["message"]


async def test_sdk_tripwire_unset_session_userdata_raises_value_error() -> None:
    session: AgentSession[Any] = AgentSession()
    with pytest.raises(ValueError):
        _ = session.userdata
    attach_flow_state(session, FlowState(current_node="s"))
    assert isinstance(session.userdata, FlowUserdata)
