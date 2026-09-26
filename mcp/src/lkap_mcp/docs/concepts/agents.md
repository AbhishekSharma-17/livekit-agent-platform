# Agents

An agent is one row: a `name`, a `pack_id` (`insurance_claim` or `generic`),
a `mode` (`prompt` or `flow`, derived from whether `config.flow` is set — you
never send `mode` yourself), and a `config` (`AgentConfig`) holding
everything else: `instructions`, `pipeline`, `voice`, `capabilities`,
`tools`, `knowledge`, `panel`, `recording`, `qa`, `flow`, `telephony`,
`pack_settings`, `timezone`, `locale` and `disclosure`. `published` gates whether `/s/{slug}` is
live; a draft agent can still be tested with `chat_start`.

`agent_list(query=, mode=, published=, archived=false)` lists every agent in
the workspace; `agent_get(id_or_slug)` reads one back.

## Creating one

`agent_create(name, template_id=None, pack_id="generic", connection_id=None,
config=None, patch=None)` seeds `config` when you omit it: from the starter
`template_id` names (`lkap://templates`), or from the pack's `PackManifest`
(`recommended_pipeline`, `default_instructions`, `default_greeting`,
`default_panel`, `kb_seeds` created and ingested synchronously — the seeded
knowledge bases are already `ready` with chunks by the time the call
returns — `tool_names` wired in). Pass `patch` to adjust the seed in the
same call; `template_id` together with `config` is refused. `connection_id`
picks which LiveKit deployment the agent's sessions run on; omit it to use
the workspace's default connection.

## Starters versus packs

A **starter template** (`StarterTemplate`) is configuration layered on a
pack: instructions, greeting, pipeline, capabilities, panel blocks, a flow,
voice settings, QA, knowledge seeds and HTTP tool seeds, all things you
could set by hand. A **pack** is code: its own tools, hooks and panel (the
`insurance_claim` pack's policy lookup and notebook). Use `template_id`;
the `insurance_claim` starter is how the code pack appears. `pack_id` alone
creates from the pack's derived starter (`pack:<pack_id>` in
`lkap://templates`), which is exactly what the manifest seeds. A starter
never fails to create: what the connection cannot run (DTMF without SIP,
recording without Egress) is switched off, and its `next_steps` say what to
add. `lkap_describe("template", id)` shows one starter as a `TemplateOut`
(the starter plus the pack it layers on).

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

## Conversation

How the agent takes turns lives in `config.pipeline`.
`conversation_preset` picks one of `patient` (waits longer before replying),
`balanced` (the LiveKit defaults), `snappy` (replies sooner and starts its
answer while the caller is still finishing), `telephony` (for phone calls:
harder to interrupt by line noise, a longer wait for slow speakers) or
`custom` (the default). A named preset is stored by name and expanded when a
session starts, so choosing one never rewrites `turn_handling`; `custom` uses
`turn_handling` as saved: `endpointing {mode, min_delay, max_delay}`,
`interruption {mode, min_duration, min_words, false_interruption_timeout,
resume_false_interruption}`, `preemptive_generation {enabled, ...}` and
`user_turn_limit`, with any newer LiveKit key passed through as is.
`turn_detector {mode: hosted|local, unlikely_threshold}` places the
end-of-turn model and sets how often it assumes the caller is not done
(higher waits more). On a phone call the `telephony` preset also switches
noise cancellation to its phone-tuned variant; that runs on LiveKit Cloud
only and is billed per minute there (`lkap_describe("provider", id)` shows
the price line). A realtime model decides turn-taking itself, so
`agent_validate` warns which preset settings it ignores. Sounds are in
`config.voice`: `thinking_sound` plays while a tool runs, `ambient_sound`
(`office_ambience`, `city_ambience`, `crowded_room`, ...) plays under the
whole call; neither plays on a typed chat.

## Date, time and timezones

An agent knows the current date and time where the caller is. Two zones are
kept apart: `config.timezone` is the **business** timezone (an IANA name such
as `Europe/London`; opening hours and bookings are written in it), and the
**caller's** timezone is chosen per session. With `config.locale.caller_timezone`
set to `detect` (the default) the caller's zone comes from their browser, else
from their phone number when it maps to one zone, else it is the business
timezone; `business` always uses the business timezone. The agent gets one
line with the date and time at the start of the call, a short time update in
long calls, and the built-ins `current_time` (the caller's time and the
business's) and `convert_time` (between zones; `caller` and `business` name the
two). A new agent created from a starter or a pack takes the workspace's
default timezone (`settings.locale.timezone`, set with the workspace settings)
when the starter sets none. `session_get` shows the zone a session used as
`caller_timezone`.

## AI disclosure and recording consent

Every agent tells callers they are talking to an AI unless you turn it off:
`config.disclosure` is `{enabled: true, text: null, position: "both"}` by
default. `position` `greeting` or `both` speaks the line at the start of the
greeting (a `{disclosure}` placeholder in `voice.greeting` marks where);
`banner` leaves it to the on-screen banner of a `consent` block, but a phone
call has no screen, so it is spoken there anyway. `text: null` uses the
workspace's wording. The workspace picks a jurisdiction (`eu`, `in` — the
default — or `us`) and may rewrite the disclosure line and the recording
question in the workspace settings (`settings.compliance`: `jurisdiction`,
`disclosure_text`, `recording_text`, `counsel_note_ack`; the console's
Settings → Compliance). The preset wording is a starting point, not legal
advice.

`config.recording.require_consent: true` records a call only after the caller
agrees: the agent asks (tap-to-accept on a `consent` block, or out loud with
`record_consent`), nothing is recorded before a yes, and a no is never
recorded; `session_get` then shows the recording's `error` as "Not recorded:
consent declined". Each answer is a `consent` session event with the SHA-256
of the exact wording. `agent_validate` warns when consent is required without
a consent block (voice answers still work), when recording is off, and when
the disclosure is turned off (naming the workspace's jurisdiction).

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
`StarterTemplate`, `TemplateOut`, `TemplatesResponse`, `Issue`.
