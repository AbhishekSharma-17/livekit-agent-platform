All gaps closed; nothing further to request. Final findings below.

# LiveKit cost-estimation research (all pages fetched 2026-09-25)

## Headline findings

- **Inference price table is obtainable programmatically, but only via an undocumented Next.js RSC flight payload.** `curl -H "RSC: 1" https://www.livekit.com/pricing/inference/` (trailing slash required; without it you get a 308) returns `text/x-component` with 137 model records containing `model_id`, `model_id_aliases`, `metering_model_id`, `provider_id`, `pricing_current.as_of` (`2026-09-21`), `pricing_current.rates[]{metric, unit{currency,measure,per}, unit_price{build,ship,scale}{amount,amount_micros}}`, `pricing_history[].effective_from`, and `quotas_current`. No documented JSON API exists; GitHub issue livekit/agents#7420 (opened 2026-09-23) requests exactly "a machine-readable pricing API" and has no maintainer reply.
- **Tiers are Build ($0/mo), Ship ($50/mo), Scale ($500/mo), Enterprise.** Inference is billed from credits: Build $2.50, Ship $5, Scale $50 included per month, then model prices (Scale gets discounted STT/TTS). LLM prices are identical across all three tiers in the payload.
- **No cost helper in the SDK.** Grep-verified negative across `metrics/usage.py`, `metrics/usage_collector.py`, `metrics/base.py`, `voice/report.py`: no `cost`/`price` field or function. The old `UsageCollector`/`UsageSummary` are deprecated in favor of `ModelUsageCollector`/`AgentSessionUsage`.
- **Cloud usage API is thin.** The only documented endpoint is the Analytics API (`https://cloud-api.livekit.io/api/project/{PROJECT_ID}/sessions[/{SESSION_ID}]`), Scale plan or higher, returning bandwidth and `connectionMinutes` per session. Agent minutes, SIP minutes, egress minutes, and Inference tokens are not exposed by any documented API.

## Table

| Item | Programmatic? | Endpoint / URL | Units and entry-tier (Build/Ship) price | Accessed | Notes |
|---|---|---|---|---|---|
| Inference price catalog | Undocumented only | `curl -H "RSC: 1" https://www.livekit.com/pricing/inference/` | see rows below | 2026-09-25 | Not a supported API; HTML page is the official source. `as_of` 2026-09-21; `pricing_history` back to 2025-09-30. |
| STT `deepgram/nova-3` | via RSC payload | pricing/inference | $0.0048/min (Scale $0.0042); Multilingual (`nova-3-multi`) $0.0058 (Scale $0.0050) | 2026-09-25 | Aliases: `deepgram/nova-3-general`, `nova3`. |
| STT `deepgram/flux-general-en` | via RSC payload | pricing/inference | $0.0065/min (Scale $0.0057) | 2026-09-25 | Label "Flux"; aliases include `deepgram/flux-general`, `deepgram/flux`. Multilingual `flux-general-multi` $0.0078/$0.0068. |
| STT `assemblyai/universal-streaming` | via RSC payload | pricing/inference | $0.0025/min (all tiers) | 2026-09-25 | Alias `assemblyai/universal-streaming-english`. |
| STT `cartesia/ink-whisper` | via RSC payload | pricing/inference | $0.0030/min (Scale $0.0023) | 2026-09-25 | |
| LLM `openai/gpt-4.1` | via RSC payload | pricing/inference | per 1M tokens: input $2.00 / cached $0.50 / output $8.00 (same all tiers) | 2026-09-25 | Two records (provider `azure` and `openai`), same price. |
| LLM `openai/gpt-4o-mini` | via RSC payload | pricing/inference | $0.15 / $0.075 / $0.60 per 1M (all tiers) | 2026-09-25 | Azure and OpenAI records, same price. |
| LLM `google/gemini-3.5-flash` | via RSC payload | pricing/inference | $1.50 / $0.15 / $9.00 per 1M (all tiers) | 2026-09-25 | Newer Flash models listed: 3.6 Flash $1.50/$0.15/$7.50; 3.7 and 3.8 Flash $0.75/$0.075/$3.75. |
| LLM `google/gemma-4-31b-it` | via RSC payload | pricing/inference | $0.40 / $0.20 / $1.20 per 1M (all tiers) | 2026-09-25 | Provider `livekit` (self-hosted by LiveKit). |
| LLM `openai/gpt-oss-120b` | via RSC payload | pricing/inference | Baseten: $0.10 / no cached rate / $0.50; Groq: $0.15 / $0.075 / $0.60 per 1M | 2026-09-25 | Two providers; default routing not shown. |
| TTS `cartesia/sonic-3` | via RSC payload | pricing/inference | $50.00 per 1M characters (Scale $37.50) | 2026-09-25 | Same for sonic-3.5 / 3.6 variants. |
| TTS `deepgram/aura-2` | via RSC payload | pricing/inference | $30.00 per 1M chars (Scale $27.00) | 2026-09-25 | |
| TTS `rime/mistv3` | via RSC payload | pricing/inference | $30.00 per 1M chars (Scale $20.00) | 2026-09-25 | Record also carries a second `character_usage` rate of $0 (renders as "$30.00 0.00" on page); meaning unclear. |
| TTS Inworld | via RSC payload | pricing/inference | `inworld/inworld-tts-2` $25 (Scale $15); `-2-flash` $15 ($9); `-1.5-max` $35 ($20); `-1.5-mini` $15 ($8) per 1M chars | 2026-09-25 | |
| Inference credits included | HTML only | https://www.livekit.com/pricing | Build $2.50, Ship $5, Scale $50 per month; then "billed based on model prices" | 2026-09-25 | Scale: "discounted model prices". Inference concurrency 5/20/50 sessions. |
| Inference metering rules | HTML only | https://docs.livekit.io/deploy/admin/billing/ | STT: seconds of connection time, 1-s increments; LLM: tokens; TTS: characters | 2026-09-25 | |
| Agent session minutes | HTML only | https://www.livekit.com/pricing | Build 1,000 incl (no overage listed); Ship 5,000 incl then $0.01/min; Scale 50,000 then $0.01/min | 2026-09-25 | Metered "in increments of 1 second with a 10-second minimum per session" (billing doc). Concurrent sessions 5/20/up to 600. |
| WebRTC participant minutes | HTML only | pricing | Build 5,000 incl; Ship 150,000 then $0.0005/min; Scale 1.5M then $0.0004/min | 2026-09-25 | |
| Downstream bandwidth | HTML only | pricing | Build 50 GB; Ship 250 GB then $0.12/GB; Scale 3 TB then $0.10/GB | 2026-09-25 | 0.01 GB minimum (billing doc). |
| SIP: US local inbound | HTML only | pricing | Build 50 min; Ship 100 then $0.01/min; Scale 1,000 then $0.01/min | 2026-09-25 | Number rental: 1 free, then $1.00/month. |
| SIP: US toll-free inbound | HTML only | pricing | $0.02/min (Ship/Scale); $2.00/month per number | 2026-09-25 | |
| SIP: third-party trunk (in + out) | HTML only | pricing | Build 1,000 min; Ship 5,000 then $0.004/min; Scale 50,000 then $0.003/min | 2026-09-25 | "Inbound and outbound minutes using a third-party SIP trunk". |
| Egress: RoomComposite/Participant transcode | HTML only | pricing | Build 60 min (shared with stream import); Ship 600 then $0.02/min video, $0.005/min audio-only; Scale 8,000 then $0.015 / $0.004 | 2026-09-25 | Track egress: 60/600/8,000 then $0.001/min. |
| Agent audio recordings (observability) | HTML only | pricing | Build 1,000 min; Ship 5,000 then $0.005/min; Scale 50,000 then $0.005/min | 2026-09-25 | Separate from Egress. Observability events: 100k / 500k then $0.00003/entry / 5M. |
| Noise cancellation (Krisp NC) | HTML only | pricing | Included on all plans (checkmarks, no quota) | 2026-09-25 | "Applies to Krisp NC and ai-coustics QUAIL_L." |
| Voice isolation (Krisp VIVA) | HTML only | pricing | Build 100 min; Ship 1,000 then $0.0012/min; Scale 10,000 then $0.0012/min | 2026-09-25 | The $0.0012 rate belongs to this row, not basic NC. |
| Analytics API | Yes (REST) | `https://cloud-api.livekit.io/api/project/{PROJECT_ID}/sessions` and `/sessions/{SESSION_ID}` | Per session: `bandwidth` (billable bytes), `connectionMinutes` (billable), participants, egress status | 2026-09-25 | Auth: LiveKit JWT with `roomList` grant. Scale plan or higher. Params `limit`, `page`, `start`, `end`; "start date must be within 7 days". Docs at docs.livekit.io/home/cloud/analytics-api/ (canonical now deploy/admin/analytics-api.md). |
| Agent Insights (Cloud UI) | UI only | https://docs.livekit.io/deploy/observability/insights/ | Spans show "token counts, durations, speech identifiers" per session | 2026-09-25 | All plans; 30-day retention; no export/API mentioned (log drains for logs only). |
| `lk` CLI usage/billing | Not found | https://docs.livekit.io/reference/developer-tools/livekit-cli/projects/ ; CLI README | — | 2026-09-25 | Only auth/project commands; README has no usage/billing/analytics command. |
| `session_usage_updated` / `session.usage` | SDK | https://docs.livekit.io/testing/observability/data/ | `AgentSessionUsage.model_usage: list[ModelUsage]`; types `LLMModelUsage`, `TTSModelUsage`, `STTModelUsage`, `InterruptionModelUsage`, `EOTModelUsage` | 2026-09-25 | Docs: for "cost estimation or billing exports". Python seconds, Node ms. |
| `ctx.make_session_report()` / `SessionReport` | SDK | `livekit-agents/livekit/agents/voice/report.py` | Fields: `job_id, room_id, room, options, events, chat_history, audio_recording_path, duration, started_at, model_usage: list[ModelUsage] \| None, sdk_version` | 2026-09-25 | `model_usage` serialized under JSON key `"usage"`. No cost. |
| Legacy `UsageCollector` / `UsageSummary` | SDK | `metrics/usage_collector.py` | Deprecated; message says use `ModelUsageCollector` | 2026-09-25 | `UsageSummary` is token/char/duration totals only; no cost field. |
| livekit-agents 1.8.3 + PR #7303 | GitHub API / PyPI | https://github.com/livekit/agents/pull/7303 ; PyPI | 1.8.3 on PyPI 2026-09-23; git tag exists; no GitHub Release object | 2026-09-25 | #7303 merged 2026-09-17, in 1.8.2...1.8.3 compare range; adds `STTModelUsage.input_audio_tokens` / `STTMetrics.input_audio_tokens` for OpenAI realtime transcription. |
| Avatar usage | SDK/docs | https://docs.livekit.io/agents/models/avatar/ | `AvatarMetrics` (join latency, playback latency) via avatar session `metrics_collected` | 2026-09-25 | No avatar entry in `AgentSessionUsage`; no avatar pricing on either pricing page. |

## Could not confirm

- A supported/documented JSON pricing endpoint. The RSC payload is unofficial and could change without notice; #7420 confirms none exists officially.
- The text of the 1.8.3 changelog (GitHub release page failed to load; release object absent from the API; `livekit-agents/CHANGELOG.md` 404). Only the commit range was verified.
- Which provider (Baseten vs Groq) `openai/gpt-oss-120b` routes to by default.
- Meaning of the second $0 `character_usage` rate on Rime records.
- A separate US-local outbound minute line item (only toll-free inbound and third-party SIP in+out appear).
- Build-plan overage for agent session minutes (none listed; presumably hard cap).
- Analytics API data delay/granularity beyond "start date within 7 days"; whether `connectionMinutes` includes agent participants.
- Any Cloud API exposing agent minutes, SIP minutes, egress minutes, or Inference tokens/credits per session or per project; per-session Inference usage appears only in the Agent Insights UI (30-day retention). GitHub issue livekit/livekit#2803 (2024) asking for a billing/usage API remains unanswered.
- The Scale-plan "Metrics export APIs" feature beyond the Analytics API (no separate docs page found).

Sources: [Inference pricing](https://www.livekit.com/pricing/inference), [Cloud pricing](https://www.livekit.com/pricing), [Billing](https://docs.livekit.io/deploy/admin/billing/), [Quotas and limits](https://docs.livekit.io/deploy/admin/quotas-and-limits/), [Analytics API](https://docs.livekit.io/home/cloud/analytics-api/), [Data hooks](https://docs.livekit.io/testing/observability/data/), [Agent insights](https://docs.livekit.io/deploy/observability/insights/), [Avatars](https://docs.livekit.io/agents/models/avatar/), [Inference docs](https://docs.livekit.io/agents/models/inference/), [PR #7303](https://github.com/livekit/agents/pull/7303), [Issue #7420](https://github.com/livekit/agents/issues/7420), [Issue #2803](https://github.com/livekit/livekit/issues/2803), [CLI projects](https://docs.livekit.io/reference/developer-tools/livekit-cli/projects/).