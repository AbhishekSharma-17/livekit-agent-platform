# Agents

An agent is one row: a `name`, a `pack_id` (`insurance_claim` or `generic`),
a `mode` (`prompt` or `flow`, derived from whether `config.flow` is set — you
never send `mode` yourself), and a `config` (`AgentConfig`) holding
everything else: `instructions`, `pipeline`, `voice`, `capabilities`,
`tools`, `knowledge`, `panel`, `recording`, `qa`, `flow`, `telephony`,
`pack_settings` and `timezone`. `published` gates whether `/s/{slug}` is
live; a draft agent can still be tested with `chat_start`.

`agent_list(query=, mode=, published=, archived=false)` lists every agent in
the workspace; `agent_get(id_or_slug)` reads one back.

## Creating one

`agent_create(name, pack_id="generic", connection_id=None, config=None,
patch=None)` seeds `config` from the pack's `PackManifest` when you omit it
(`recommended_pipeline`, `default_instructions`, `default_greeting`,
`default_panel`, `kb_seeds` created and ingested synchronously — the seeded
knowledge bases are already `ready` with chunks by the time the call
returns — `tool_names` wired in). Pass `patch` to adjust the seed in the
same call. `connection_id`
picks which LiveKit deployment the agent's sessions run on; omit it to use
the workspace's default connection.

## Editing

`agent_update(id_or_slug, patch={...})` is a merge patch (RFC 7386: `null`
removes a key) applied over the *current stored* `config`, then validated
(`validate_first=true` by default) and saved as a new `config_version`. Send
`config={...}` instead for a full replacement. A patch that fails validation
is not saved — you get the `issues` back; pass `save_with_errors=true` only
when you deliberately want a broken draft saved (the console does this too).

Re-read with `agent_get(id_or_slug, include_config=true)` before patching
something another editor (the console, a teammate) may have touched — a
merge patch has no concurrency check, so a stale read can clobber a concurrent
edit.

## Validating, publishing, versioning

- `agent_validate(id_or_slug)` runs the full `ValidationResult` (pipeline
  slot completeness via `pipeline_issues`, provider/credential checks, panel
  block config checks, flow structural checks) without saving anything.
- `agent_publish(id_or_slug, published=true)` flips the flag; the response
  includes the public session URL.
- `agent_archive(id_or_slug, confirm=true)` — needed before
  `lkap_delete(kind="agent", id, confirm=true)` will remove an agent **that
  has sessions**; a session-less, unarchived agent deletes straight away.
  Either way, the api's 409 (when one comes back) names the reason.
  `agent_versions(id_or_slug)` lists prior `config_version`s and
  `restore=n` (with `confirm=true`) rolls back to one.

## Attaching things

`agent_attach(id_or_slug, kb_ids=[...], tool_ids=[...])` patches
`config.knowledge.kb_ids` / `config.tools.tool_ids` and re-validates in one
call; `remove=true` detaches instead. `agent_limits(id_or_slug)` reads
(both `limits` and `allowed_origins` omitted) or writes the per-agent
`AgentLimits` (`max_concurrent_sessions`, `max_session_duration_s`,
`rate_per_ip_per_min`, `rate_per_agent_per_min`) and the session page's CORS
allowlist.

## Prompt vs. flow

A **prompt** agent is one system prompt (`config.instructions`) plus tools
and knowledge; a **flow** agent replaces that with a node graph
(`lkap_explain("flows")`). Switch with `agent_update(patch={"flow":
{...FlowSpec...}})`; switch back with `patch={"flow": null}`. Validate a flow
before saving it with `agent_flow_validate(id_or_slug, flow={...})` — it
checks structure and references without writing anything.

## Related tools

`agent_list`, `agent_get`, `agent_create`, `agent_update`, `agent_validate`,
`agent_publish`, `agent_archive`, `agent_versions`, `agent_attach`,
`agent_limits`, `agent_flow_validate`, `lkap_delete`.

## Related schemas

`AgentConfig`, `AgentCreate`, `AgentUpdate`, `AgentOut`, `AgentPublicOut`,
`AgentLimits`, `ConfigVersionOut`, `ValidationResult`, `PackManifest`,
`Issue`.
