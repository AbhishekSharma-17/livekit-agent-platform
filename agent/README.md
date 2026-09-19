# lkap_agent — LiveKit worker

Single AgentServer, agent_name `lkap-agent` (explicit dispatch). Builds provider stack per job from ResolvedAgentConfig. See docs/ARCHITECTURE.md §3-§9, docs/CONTRACTS.md.
Run: `uv run python -m lkap_agent.main dev` (with `LIVEKIT_URL/API_KEY/API_SECRET`, `LKAP_API_BASE_URL`, `LKAP_SERVICE_TOKEN` in env). The name `lkap-agent` is fixed in code (DECISIONS-W2 §D-W2-11); `LIVEKIT_AGENT_NAME` is optional and, if set, must equal `lkap-agent`. No hot reload: restart after code edits with SIGINT, wait ≥ 15 s, then SIGKILL (D-W2-13, docs/RUNBOOK.md).
