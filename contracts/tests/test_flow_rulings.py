"""Contract changes from rulings R-V2-8 and R-V2-11 (V2-16)."""

from __future__ import annotations

from lkap_contracts.agent_config import AgentConfig, PipelineConfig, ProviderRef, QaConfig, effective_qa
from lkap_contracts.api_models import SessionSummaryIn
from lkap_contracts.flow import AgentNode, FlowEdge, FlowSpec, QaNode, StartNode


def _config(*, qa: QaConfig | None = None, flow: FlowSpec | None = None) -> AgentConfig:
    return AgentConfig(instructions="hi", pipeline=PipelineConfig(), qa=qa or QaConfig(), flow=flow)


def _flow(*, rubric: str | None = None, with_qa: bool = True) -> FlowSpec:
    nodes: list[StartNode | AgentNode | QaNode] = [
        StartNode(id="start"),
        AgentNode(id="ask", instructions="Ask."),
    ]
    if with_qa:
        nodes.append(QaNode(id="qa", rubric_prompt=rubric))
    return FlowSpec(nodes=nodes, edges=[FlowEdge(id="e1", source="start", target="ask")])


def test_effective_qa_turns_qa_on_for_a_flow_with_a_qa_node() -> None:
    qa = effective_qa(_config(flow=_flow(rubric="Score empathy.")))
    assert qa.enabled is True
    assert qa.rubric_prompt == "Score empathy."


def test_effective_qa_keeps_the_stored_rubric_and_model_when_the_node_has_none() -> None:
    model = ProviderRef(provider_id="openai-llm", credential_id="c1")
    config = _config(qa=QaConfig(enabled=False, rubric_prompt="Stored.", model=model), flow=_flow())
    qa = effective_qa(config)
    assert (qa.enabled, qa.rubric_prompt, qa.model) == (True, "Stored.", model)


def test_effective_qa_returns_the_stored_settings_without_a_qa_node() -> None:
    stored = QaConfig(enabled=False, rubric_prompt="x")
    for config in (_config(qa=stored), _config(qa=stored, flow=_flow(with_qa=False))):
        assert effective_qa(config) is config.qa


def test_session_summary_carries_flow_disposition_and_variables_with_prompt_defaults() -> None:
    prompt = SessionSummaryIn(status="ended", usage={}, transcript=[])
    assert (prompt.disposition, prompt.variables) == (None, {})
    flow = SessionSummaryIn.model_validate(
        {"status": "ended", "usage": {}, "transcript": [], "disposition": "done", "variables": {"n": 1}}
    )
    assert (flow.disposition, flow.variables) == ("done", {"n": 1})
