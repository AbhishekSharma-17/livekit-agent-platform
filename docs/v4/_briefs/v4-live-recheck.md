# V4 live recheck: the Simli avatar (B-1) and the LiveKit phone number

**Date:** 2026-09-25. First run 03:55–04:05 IST (22:25–22:35 UTC); second run 04:11–04:19 IST (22:41–22:49 UTC), after the B-12 fix.
**Driver:** Opus 5.5, log-only (R-V4-20: no source edits, no commits, no restarts).
**Target:** the user's dev stack:
- api on `:8080`, the user's own DB;
- web on `:3000`;
- the `lkap-agent` worker the user restarted at 03:52:23 IST on HEAD `19a907d`, which includes `32750e6` (the V4-06 bug fixes) and the V4-11 merge.

**Scratch files:** `<scratchpad>/v411/`, which holds `logs/`, `out/` and `snap/`.

The repo is public. This file contains no token, key, host or phone number.

| # | Check | Result |
|---|---|---|
| 1 | Simli avatar on `/s/demo-survey-intake-form` | **PASS on the second run (04:12 IST).** Video **7.0 s** after Start call, at **360×360**; the agent greeted. The first run (03:59) failed on **B-12** (`_asks.md` #54, now resolved in `3d9b8c0`), not on B-1. See §1a |
| 3 | LiveKit number: assign it to Demo — Phone agent | **Still OFFLINE.** One assignment attempt was made, and LiveKit refused it (422). LKAP rolled the attempt back cleanly |

Check 2 (V4-11 live steps 1–7) is in `v4-11-live.md`. It was blocked by B-12 on the first run and **passed all five checks** on the second. Check 3 was not re-run on the second pass: the number is still OFFLINE on LiveKit's side.

## 1. Simli avatar (B-1 recheck), first run: failed on B-12

**Run.** The run reused V4-06's `avatar.mjs`: Playwright Chromium with `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream`, one `Start call`, then a poll once a second for 60 s for a live remote `<video>`, then `END CALL`.

**Result:**
- **No video within 60 s.** Time to video and resolution: none.
- The page showed "Demo — Survey / intake form couldn't join the call — Agent did not join the room." The chip read **Failed**. See `out/c1-simli-live.png`.
- The agent never greeted.
- Simli minutes used: **0**. The job crashed before `_start_avatar_or_degrade`, so no Simli session was requested.

**Session.** `0e87a488a63f422784e837eefcb0fda7`, `channel=web`, agent `ea0f9f2a…` (config_version 7).

**Worker log, sanitised** (the full slice is in `logs/worker-crash.log`):

```text
22:29:09.658Z job accepted  agent_id=ea0f9f2a… channel=web config_version=7 session_id=0e87a488…
22:29:09.727Z session built mode=cascaded has_stt=True has_tts=True text_only=False
22:29:09.750Z [error] unhandled exception while running the job task
  File "agent/src/lkap_agent/main.py", line 715, in run_session
      ctx.add_shutdown_callback(on_shutdown)
  File ".../livekit/agents/job.py", line 632, in add_shutdown_callback
      if callback.__code__.co_argcount >= min_args_num:
  AttributeError: '_OnceShutdown' object has no attribute '__code__'. Did you mean: '__call__'?
03:59:09,759 process exiting {"reason": "job crashed", ...}
```

**Session events.** `GET /v1/sessions/0e87a488…/events` returns **no events**: the job died before the observer posted anything. `GET /v1/sessions/0e87a488…` still reads `status=active`, `ended_at=null`, 6 minutes later (B-13, `_asks.md` #55). The voice-only warning event ("The avatar could not start; the call continues voice-only.") did **not** appear, because that code was never reached.

**Not specific to Simli.** The worker accepted three jobs after its restart, and all three crashed on the same line:
- `9d151645…` and `f01a0a72…`: the user's own console tests of Demo — Vision assistant (Beyond Presence), `channel=test`, at 22:27:19Z and 22:27:42Z, before this run started;
- `0e87a488…`: this run.

`run_session` registers the callback on every channel before `_start`. The only early returns are for config or build failures, so text chat, the console test, web and SIP calls all fail. **Every live session on this worker fails until B-12 is fixed and the worker is restarted.**

**Cause (from reading the code; nothing was fixed).** Commit `32750e6`, the B-8 fix, wraps the job's shutdown callback in `_OnceShutdown` (`main.py:1116`), a class with an `async __call__`. It registers the instance directly with `ctx.add_shutdown_callback(on_shutdown)`. In livekit-agents 1.8.2, `JobContext.add_shutdown_callback` reads `callback.__code__.co_argcount` to decide whether to pass the reason, and a class instance has no `__code__`.

The unit gate missed it because `agent/tests/unit/test_main.py:106`'s fake `add_shutdown_callback(callback: Any)` accepts any callable.

Two fixes would work:
- Register a nested function, `async def _on_shutdown(reason: str) -> None: await on_shutdown(reason)`.
- Register the bound method `on_shutdown.__call__`, which has `__code__` and an argcount of 2.

Add a test whose fake reproduces the SDK's `__code__` check.

**What is still to verify after the fix and a worker restart:**
- B-1: the Simli nested `SimliConfig` build;
- asks #26: the voice-only degrade and its warning event;
- the time to video and the resolution.

Rerun `node <scratchpad>/v411/avatar.mjs demo-survey-intake-form <out>`, which uses at most about 60 s of Simli time.

## 1a. Simli avatar, second run: PASS

- **Setup.** The coordinator fixed B-12 in `3d9b8c0`: `run_session` now registers a plain `async def _run_shutdown(reason)`. The worker was restarted by the coordinator (PID 30659, registered 04:10:29 IST, 33 providers). Before the avatar test, a text chat on Demo — Blank agent (`3b381f59…`) confirmed that jobs start and end.
- **Run.** The same `avatar.mjs`, at 22:42:33Z.

| Metric | Value |
|---|---|
| Time to video | **7.0 s** after Start call: clicked at 1.4 s, a live remote `<video>` at 8.4 s |
| Resolution | **360×360** (`videoWidth`×`videoHeight` when first observed) |
| Greeting | Spoken and transcribed: "Hi! This is a two-minute feedback survey — five …" (`session_get` transcript, 1 assistant turn) |
| Session | `103dafd9c4a14fc495cda7012ef4cb2d`, `channel=web`, **ended**, 22:42:37–22:42:46Z (9.7 s) |
| Simli time | about **10 s** (END CALL clicked at 12.5 s) |
| Voice-only fallback | **Not triggered**, as expected: the avatar started. No `error`/warning event; the event types were `agent_state`, `session_started`, `metrics`, `agent_turn`, `session_ended` |
| Worker log | No Simli or avatar error. The only warning: `resume_false_interruption is enabled but audio output does not support pause` (the avatar's audio sink) |

Screenshot: `<scratchpad>/v411/out/c1b-simli-live.png`. It shows the Simli face "Tina", the **Live** chip and the greeting in the transcript. Not checked: the voice-only degrade path (asks #26) under a real avatar failure. It would take a deliberately broken avatar config on a demo agent, and nothing needed it.

**B-1 is verified fixed live:** the nested `simli_config.face_id` reaches `SimliConfig`.

Side notes, not bugs:
- The session panel's Status block reads "Not started" while the call is live. V4-06 recorded the same on the Bey call.
- The survey session's usage line shows STT/TTS usage only, costed at $0.0002.

## 3. The LiveKit phone number

| Step | Result |
|---|---|
| `lk number list --json` (read-only) | 1 number, `PN_PPN_EU4JBkgbNdCN`, Highfield MD, `voice`: **`PHONE_NUMBER_STATUS_OFFLINE`**, no dispatch rule. `totalCount=1`, `offlineCount=1`. Same as V4-05 |
| `POST /v1/telephony/numbers/refresh {"connection_id": "ff57601e…"}` | 200: `seen 1, added 0, updated 0, released 0`, warning "1 number(s) offline in LiveKit" |
| `PUT /v1/telephony/numbers/8a670475… {"inbound_agent_id": "174fecb8…" (Demo — Phone agent), "label": "Demo phone line"}`: **one attempt**, as the brief allows when the number is still OFFLINE | **422** `unprocessable_entity`: "LiveKit rejected attaching the phone number: twirp error unknown: Failed to update phone number", `details.livekit_code=invalid_argument`, `http_status=400` |
| Routing state afterwards | LKAP: `attach_state=offline`, `lk_status=offline`, `lk_inbound_status=unknown`, `inbound_agent_id=null`, `dispatch_rule_id=null`, `label=""` (the label was not saved either). `GET /v1/telephony/dispatch-rules` → 0 items. LiveKit: `lk sip dispatch list` → 0 rules, and the number has no `sipDispatchRuleId` |

**Reading.** `assign_number` created the trunk-less managed rule. LiveKit then refused `UpdatePhoneNumber`, and LKAP deleted the rule again, as designed (`service.py:1019`). **Nothing was left behind**, either in LKAP or in the LiveKit project.

The refusal matches the earlier attempt. As long as LiveKit reports the number `OFFLINE`, it won't take a dispatch rule. That is an account or number-provisioning state on LiveKit's side, not an LKAP bug.

**For the user:** check the number's state in the LiveKit dashboard, or ask LiveKit support why a purchased number stays `OFFLINE`. Once `lk number list` shows `ACTIVE`, the same `PUT` should attach it; that is V4-05 live step 2.

The run neither bought nor released anything, and placed no call.

## 4. Safety and cleanup

- **Key.** One Builder key was minted for check 2:
  - name `v411-live`, id `6c15ce92…`, prefix `lkap_o4V`;
  - scopes: `agents:read/write`, `sessions:read/write`, `connections:read`, `providers:read`, `audit:read`;
  - expiry +1 day.

  It was held only in a 0600 MCP config and used for one `list_tools` call. It was **revoked**: `DELETE` → 204, and `GET /v1/api-keys` shows `revoked_at` 22:34:16Z. The config file was deleted, and the key was never printed.
- **Admin-token writes (break-glass):** the key mint and revoke, the number refresh, and the one refused number `PUT`. Nothing else.
- **Before/after snapshots** (`snap/v411-before.json` and `snap/v411-after.json`):
  - Agents (19), KBs (18), tools (12), webhooks (0) and connections (1) are **identical**.
  - API keys: +1 (`v411-live`, revoked).
  - Telephony: only the number's `lk_synced_at`, from the refresh.
  - Provider keys: +1 `openrouter-llm` key (`46d4cd09…`), created at 22:28:52Z through the console (break-glass audit row `POST /v1/credentials`). That was the **user**, during the run; this run didn't create it.
- **Not touched:** no restart or signal to the worker (PID 20725), the api or web; no `.env` read; no path starting with "credentials"; `sqb-assistant` and every non-demo agent untouched.
- **What stays in the user's DB:** web session `0e87a488…` (stuck `active`, B-13). The user's own sessions `9d151645…` and `f01a0a72…` are in the same state.
- **Second run (04:11–04:19 IST).** The driver minted key `v411-live-2` (`47ce4e75…`, prefix `lkap_t2O`) and revoked it at the end (`revoked_at` 22:48:18Z); the MCP config was deleted.
  - Before/after diff: only Demo — Receptionist and Demo — Lead qualification changed (`config_version` 6→7 and 5→7; V4-11 steps 1 and 3), plus the new revoked key. The `Demo — Router scratch` agent (`49b8eb3a…`) was created and purged inside the run.
  - Nothing else changed: no restart or signal, no `.env` read, no `credentials*` path, and `sqb-assistant` and all non-demo objects untouched.
- **#55 stuck sessions, checked 22:48Z and not modified.** **Seven** sessions from the crash window are still `status=active` with `ended_at=null`:
  - web: `0e87a488…` (this run);
  - console `test`, 22:27–22:28Z: `9d151645…` and `f01a0a72…`;
  - console `test`, 22:36–22:39Z: `a6bb039c…`, `1e958d6a…`, `c0f8d39b…` and `9e4ca9c5…`. These four came from the user's own console tests after the first report and before the fixed worker registered at 22:40:29Z.

  None has been reaped. Every session started on the fixed worker ended normally.
- **Cost:** $0 of Claude in both runs; no `claude -p` run. First run: one crashed web job and 0 s of Simli. Second run: about 10 s of Simli and one web session ($0.0002). The V4-11 chats cost about $0.014 of LiveKit Inference, smoke chat included.
