# Beside-the-agent UI and advertised capabilities — 12 voice/video agent products

Method note: every bullet cites a page I fetched today. Where a claim only came from a search-engine snippet (not a fetched page), or the page 404'd, it is marked UNVERIFIED. Identifiers in backticks are exact API/UI names from the fetched pages; everything else is paraphrased.

---

## 1. OpenAI Realtime — `openai-realtime-agents` and `openai-realtime-console`

**(a) What renders beside the agent**
- `openai-realtime-agents`: two-pane layout — conversation transcript on the left (includes tool calls, tool-call responses and agent changes), event log on the right showing client and server events; a Scenario dropdown (top right) and an Agent dropdown to switch agents in a scenario (https://raw.githubusercontent.com/openai/openai-realtime-agents/main/README.md, accessed 2026-09-24).
- Bottom toolbar labels: `Connect`/`Connecting...`/`Disconnect`, a `Push to talk` toggle with a `Talk` button, `Audio playback` toggle, `Logs` toggle, and a `Codec:` selector with `Opus (48 kHz)`, `PCMU (8 kHz)`, `PCMA (8 kHz)` (PSTN simulation) (https://raw.githubusercontent.com/openai/openai-realtime-agents/main/src/app/components/BottomToolbar.tsx, accessed 2026-09-24).
- Transcript component: role-aligned bubbles, `BREADCRUMB` items with timestamp + title and an expandable ▶ arrow that opens a JSON `<pre>` (used for tool calls / agent changes), per-item timestamps, a `Copy` button, a `Downloaded Audio` button, a text input with placeholder `Type a message...`, and `isHidden` items suppressed (https://raw.githubusercontent.com/openai/openai-realtime-agents/main/src/app/components/Transcript.tsx, accessed 2026-09-24).
- Guardrail chip under assistant messages with states `Pending` (IN_PROGRESS), `Pass` (category NONE) or `Fail`; clicking Pass/Fail expands to show a "Moderation Category", rationale text and optional tested text (https://raw.githubusercontent.com/openai/openai-realtime-agents/main/src/app/components/GuardrailChip.tsx, accessed 2026-09-24). The README describes the mechanism: messages are marked in-progress when `response.text.delta` starts and flip to FAIL/PASS on `guardrail_tripped` / `response.done` (README URL above, accessed 2026-09-24).
- Events panel: heading `Logs`, ▲ for client events (purple), ▼ for server events (green), event name (red when it contains "error"), right-aligned timestamp, expandable 2-space JSON (https://raw.githubusercontent.com/openai/openai-realtime-agents/main/src/app/components/Events.tsx, accessed 2026-09-24).
- Component directory confirms the full set: `BottomToolbar.tsx`, `Events.tsx`, `GuardrailChip.tsx`, `Transcript.tsx` (https://github.com/openai/openai-realtime-agents/tree/main/src/app/components, accessed 2026-09-24).
- `openai-realtime-console` (WebRTC version): README describes a logging panel for client/server JSON event payloads; components are `App.jsx`, `Button.jsx`, `EventLog.jsx`, `SessionControls.jsx`, `ToolPanel.jsx` (https://raw.githubusercontent.com/openai/openai-realtime-console/main/README.md and https://github.com/openai/openai-realtime-console/tree/main/client/components, accessed 2026-09-24).
- `ToolPanel.jsx` is the canonical "tool output as a side panel" example: heading `Color Palette Tool`, placeholder text asking for palette advice, a `display_color_palette` function whose result renders as colour swatches with hex codes plus the raw function-call JSON (https://raw.githubusercontent.com/openai/openai-realtime-console/main/client/components/ToolPanel.jsx, accessed 2026-09-24).

**(b) Advertised agent patterns**
- Three patterns: Chat-Supervisor (realtime model chats; a text supervisor model handles tool calls — the user hears deferral phrases such as "Let me think"), Sequential Handoff (specialist agents transfer via tool calls, e.g. `transferAgents()`), and background Escalation to a reasoning model (README URL above, accessed 2026-09-24).
- Output guardrails/moderation are checked before an assistant message is shown in the UI (README URL above, accessed 2026-09-24).

---

## 2. Vapi

**(a) What renders beside the agent**
- Web SDK events available to build a UI: `call-start`, `call-end`, `speech-start`, `speech-end`, `message` (carries transcript with role), `volume-level`. The web quickstart's sample UI has a fixed "Talk to Assistant" button, a pulsing call-status indicator, a transcript area with chat-style bubbles, status text ("Assistant Speaking…"/"Listening…") and an End call button (https://docs.vapi.ai/quickstart/web, accessed 2026-09-24).
- Hosted web widget: Voice Mode and Chat Mode (text), floating button, transcript display, microphone controls, consent management, avatar support; embed attributes include `public-key`, `assistant-id`, `mode`, `theme`, `position`, `size`, `base-color`, `accent-color`, plus text labels and consent requirements (https://docs.vapi.ai/chat/web-widget, accessed 2026-09-24).
- The docs-agent example points to a widget with waveform visualization and real-time transcripts (https://docs.vapi.ai/assistants/examples/docs-agent, accessed 2026-09-24).

**(b) Advertised capabilities**
- Assistants (single prompt + tools + structured outputs) and Squads (multi-assistant with context-preserving transfers) (https://docs.vapi.ai/quickstart/introduction, accessed 2026-09-24).
- Workflows (visual node/edge builder) are being retired on August 18, 2026 with a migration guide to Squads/Assistants; Vapi no longer recommends them for new builds (https://docs.vapi.ai/workflows/overview, accessed 2026-09-24).
- `startSpeakingPlan` (`waitSeconds`, `smartEndpointingPlan`, `transcriptionEndpointingPlan`) and `stopSpeakingPlan` (`numWords`, `voiceSeconds`, `backoffSeconds`) for barge-in tuning (https://docs.vapi.ai/customization/speech-configuration, accessed 2026-09-24).
- `backgroundSound` (default `office` for phone, `off` for web), `backgroundSpeechDenoisingPlan`, `voicemailDetection` (off by default), `analysisPlan` (marked deprecated), `artifactPlan`, `compliancePlan.hipaaEnabled` / `pciEnabled` (PCI mode stores no logs or transcripts), `observabilityPlan` (Langfuse), `hooks`, `monitorPlan.listenEnabled` / `controlEnabled` (live listen-in and live control), `firstMessageMode`, `keypadInputPlan` (DTMF) (https://docs.vapi.ai/api-reference/assistants/create, accessed 2026-09-24).
- Denoising: Smart Denoising (Krisp) and experimental Fourier denoising with environment presets (https://docs.vapi.ai/documentation/assistants/conversation-behavior/background-speech-denoising, accessed 2026-09-24).
- Voicemail detection providers: Vapi (recommended), Google, OpenAI, Twilio; resumes naturally if a human picks up mid-message (https://docs.vapi.ai/calls/voicemail-detection, accessed 2026-09-24).
- Transfer modes: `blind-transfer`, `blind-transfer-add-summary-to-sip-header` (`X-Transfer-Summary`), `warm-transfer-say-message`, `warm-transfer-say-summary`, `warm-transfer-twiml`, two wait-for-operator variants, and `warm-transfer-experimental` (fallback, summaries, custom hold audio, transfer-assistant conversation) (https://docs.vapi.ai/tools/transfer-call, accessed 2026-09-24).
- Evals: JSON mock conversations with per-turn judge plans (exact, regex, AI judge), targeting an Assistant or a Squad, run from dashboard or API (https://docs.vapi.ai/observability/evals-quickstart, accessed 2026-09-24). Simulations: AI caller with selectable personality, structured-output pass/fail, suites; chat sims are cheaper, voice sims exercise speech and turn-taking (https://docs.vapi.ai/observability/simulations-quickstart and https://docs.vapi.ai/test/test-suites, accessed 2026-09-24).
- HIPAA switch under dashboard Add-ons; PHI only via the /call endpoint (found via search: https://docs.vapi.ai/security-and-privacy/hipaa — page not fetched, UNVERIFIED detail).
- A/B experiments / traffic splitting: UNVERIFIED — two domain-pinned searches surfaced no such feature. Filler-word injection: UNVERIFIED — not on the speech-configuration page.

---

## 3. Retell AI

**(a) What renders beside the agent**
- Website widget: floating action button opening a chat interface (text), voice+chat hybrid with tab switching, or a callback form; branding options; conversation history kept locally; embedded via a single script tag with `data-public-key` / `data-voice-public-key`, `data-agent-id`, `data-voice-agent-id`, `data-widget="callback"`, colour attributes and `data-recaptcha-key` (https://docs.retellai.com/deploy/chat-widget, accessed 2026-09-24).
- Web SDK for browser calls and a web-call test mode; the test-web page describes what a web call exercises (latency, turn-taking, interruptions, voice quality) but does not describe any information panels (https://docs.retellai.com/test/test-web, accessed 2026-09-24).
- Docs index positions Web SDK and website widget under Deploy, conversation flow (drag-and-drop nodes) under Build, and simulation testing under Test (https://docs.retellai.com/, accessed 2026-09-24).

**(b) Advertised capabilities**
- Agent fields: `enable_backchannel` / `backchannel_frequency` / `backchannel_words`, `ambient_sound` + `ambient_sound_volume`, `voicemail_option` (detects in the first 3 minutes), `interruption_sensitivity`, `responsiveness`, `language` (single locale or array for multilingual), `pii_config`, `post_call_analysis_data`, `denoising_mode`, `reminder_trigger_ms`, `boosted_keywords`, `end_call_after_silence_ms`, `allow_user_dtmf` (https://docs.retellai.com/api-references/create-agent, accessed 2026-09-24).
- Transfer: cold vs warm; whisper debrief message spoken privately to the destination; queue-question option so the agent waits for a real person; optional connect tone and a message heard by both parties; SIP URI destinations (https://docs.retellai.com/build/single-multi-prompt/transfer-call, accessed 2026-09-24). Warm Transfer 2.0 (July 7, 2025) added automatic human detection, whisper messages and three-way messages; Analytics Dashboard 2.0 charts post-call fields (https://www.retellai.com/changelog/major-platform-upgrades-knowledge-base-warm-transfer-voice-analytics, accessed 2026-09-24).
- Simulation testing: LLM plays the caller from a user prompt, success criteria produce one pass/fail, tool mocks intercept function calls, batch runs land in Batch Testing History, and "Conductor" generates tests from real calls (https://docs.retellai.com/test/llm-simulation-testing, accessed 2026-09-24).
- Post-call extraction categories: Text, Selector, Boolean, Number, evaluated by an LLM (https://docs.retellai.com/features/post-call-analysis, accessed 2026-09-24).
- Data storage tiers: Everything / Everything except PII / Basic Attributes Only; 13 PII categories scrubbed (names, addresses, emails, SSN, passport, licence, cards, bank, passwords, PINs, medical IDs, DOB, account numbers) (https://docs.retellai.com/accounts/privacy-disable, accessed 2026-09-24).
- SMS channel and outbound batch calling: UNVERIFIED (search snippets only; what I verified is batch *testing*, a different feature).

---

## 4. Bland AI

**(a) What renders beside the agent**
- Web chat widget: chat interface, `request_data` for personalisation, custom components embedded as iframes at conversation points, live-agent escalation via webhooks (Zendesk/Intercom), post-conversation webhooks, visual editor for appearance, visitor tracking. Voice input is NOT on this page (https://docs.bland.ai/tutorials/chat-widget, accessed 2026-09-24).
- Product page for web chat: drop-in embed, knowledge-base grounding that cites sources, human escalation with transcript and context; the chat surface is described as where the customer signs and sees "the artifact" after voice/iMessage steps (https://www.bland.ai/product/chat, accessed 2026-09-24).
- "Bland Web" as a product name: not found; "web" appears only as a channel in the docs index (https://docs.bland.ai/, accessed 2026-09-24).

**(b) Advertised capabilities**
- Conversational Pathways node types: Default, Webhook, Knowledge Base, End Call, Transfer Call, Wait for Response; Global Nodes; conditions for routing; per-node variable extraction referenced as `{{variable_name}}` (https://docs.bland.ai/tutorials/pathways, accessed 2026-09-24).
- Warm transfer: proxy agent number, AI briefs the human using a briefing prompt, answering-machine detection and wait-for-greeting for IVR/queue, calls merge and hold music ends (https://docs.bland.ai/tutorials/warm-transfer, accessed 2026-09-24).
- Memory: agents identify customers by phone number and pull prior-interaction context (blog dated March 7, 2025) (https://www.bland.ai/blogs/memory-slack-hubspot-integrations, accessed 2026-09-24); changelog July 6, 2026: memory syncs to CRM contacts (HubSpot/Salesforce) (https://www.bland.ai/changelog, accessed 2026-09-24).
- Messaging: SMS, RCS and iMessage from one stack with shared cross-channel history (https://docs.bland.ai/tutorials/messaging/overview, accessed 2026-09-24).
- Changelog 2026 items: adaptive response timing per caller (Aug 3), interruptibility + resumption-speed controls (Jul 21), Evals with LLM judges over call history (May 28), iMessage channel and Knowledge Map (May 7), metric alerts (Apr 30) (https://www.bland.ai/changelog, accessed 2026-09-24).
- Voice cloning: UNVERIFIED (voices API page 404'd). "Citrus": UNVERIFIED — three searches (bland.ai-pinned and open web) found no such product/feature.

---

## 5. Synthflow AI

**(a) What renders beside the agent**
- Voice Widget: drop-in embeddable voice agent for websites needing no custom code; alternatively a WebSocket integration streams audio both ways inside your own app (https://docs.synthflow.ai/ws-media-integration, accessed 2026-09-24). Neither fetched page describes the widget's visual elements — UNVERIFIED.
- AI transparency: configurable greeting and consent messages, with sample disclosure lines for inbound and outbound calls; deployer is responsible per jurisdiction (EU AI Act Art. 50 cited) (https://docs.synthflow.ai/ai-transparency, accessed 2026-09-24).

**(b) Advertised capabilities**
- Call transfers: TEL, SIP, dynamic (from pre-call webhook), phone books; cold (SIP REFER), warm-with-message (whisper), warm-with-summary (AI summary); Human Detection waits for a live person before the whisper; stage messages during the transfer (https://docs.synthflow.ai/call-transfers, accessed 2026-09-24).
- Agent editor: Actions drawer (bookings, transfers, API calls, SMS, extractors) and noise cancellation under Call Configuration (https://docs.synthflow.ai/the-agent-editor, accessed 2026-09-24).
- Real-time booking: Cal.com and GoHighLevel named; checks availability, proposes slots, books, sends confirmations (https://docs.synthflow.ai/create-a-real-time-booking-action, accessed 2026-09-24).
- Voicemail Detection setting exists (FAQ notes some voicemails pass through) (https://docs.synthflow.ai/, accessed 2026-09-24).
- Whitelabel/agency: custom domain, branding, subaccounts with limits and permissions, reselling (http://docs.synthflow.ai/whitelabel-solution-overview, accessed 2026-09-24).
- HIPAA/SOC 2, in-call SMS/WhatsApp: UNVERIFIED (search snippets only).

---

## 6. Hume AI EVI

**(a) What renders beside the agent**
- Official Next.js starter components: `Chat.tsx`, `Controls.tsx`, `Expressions.tsx`, `Messages.tsx`, `MicFFT.tsx`, `StartCall.tsx` (https://github.com/HumeAI/hume-evi-next-js-starter/tree/main/components, accessed 2026-09-24). `Expressions.tsx` renders the top 3 expressions per message as labelled bars whose width tracks the 0–1 score, with two-decimal values and per-expression colours (https://raw.githubusercontent.com/HumeAI/hume-evi-next-js-starter/main/components/Expressions.tsx, accessed 2026-09-24). This is the starter, not hume.ai's hosted demo.
- EVI Playground exists in the Hume platform for trying features (https://dev.hume.ai/docs/speech-to-speech-evi/overview, accessed 2026-09-24).

**(b) Advertised capabilities**
- Streaming measurements of tune, rhythm and timbre; responds to expression; always interruptible; design, clone or select a voice (https://dev.hume.ai/docs/speech-to-speech-evi/overview, accessed 2026-09-24).
- Versions: EVI 3 and EVI 4-mini supported; EVI 1/2 reached end of support August 30, 2025; 4-mini is multilingual (11 languages) and requires a supplemental LLM; EVI 3 delivers prosody asynchronously (https://dev.hume.ai/docs/empathic-voice-interface-evi/evi-2, accessed 2026-09-24). No plain "EVI 4" found.
- FAQ: expression labels are model confidence in perceived expression, not actual feeling; webhooks and tool use; supplemental LLMs from Anthropic/OpenAI/Google; Chat History API returns transcripts, expression measurements and audio reconstruction (https://dev.hume.ai/docs/speech-to-speech-evi/faq, accessed 2026-09-24).
- Product page: interruptibility, programmatic pause/resume, resume chats with context, chat history with emotion data, dynamic variables, mid-conversation context injection for RAG, tool use, voice library/clone/design, any LLM (https://www.hume.ai/empathic-voice-interface, accessed 2026-09-24).
- Voice library of 100+ styles, voice design, voice cloning from recording or file (https://dev.hume.ai/docs/empathic-voice-interface-evi/voices, accessed 2026-09-24).
- Expression Measurement API details (48 prosody dimensions, streaming): UNVERIFIED — three candidate doc URLs 404'd.

---

## 7. Tavus CVI

**(a) What renders beside the agent**
- Default UI is Daily Prebuilt, customisable with `showLeaveButton`, `showFullscreenButton`, language and theme colours (https://docs.tavus.io/sections/conversational-video-interface/quickstart/customize-conversation-ui, accessed 2026-09-24).
- Conversations return a `conversation_url` on daily.co; you can use the default video interface, customise the Daily UI, or embed CVI in your app; recording to your own S3 (https://docs.tavus.io/sections/conversational-video-interface/conversation/overview, accessed 2026-09-24).
- Screen share: the Conversation block includes a share control, or add `ScreenShareButton` from `@tavus/cvi-ui`; Raven reads on-screen text, spots errors and follows walkthroughs; needs a live video room (https://docs.tavus.io/sections/conversational-video-interface/pal/screen-share, accessed 2026-09-24).
- Interactions protocol for custom UIs: echo, respond (typed message), interrupt, tool-call result; observable events include utterance, utterance streaming, tool call, perception tool call, perception analysis, and started/stopped speaking with `participant_id` (https://docs.tavus.io/sections/conversational-video-interface/interactions-protocols/overview, accessed 2026-09-24).

**(b) Advertised capabilities**
- Pipeline: Raven (perception: expressions, gaze, background, screen), Sparrow (turn-taking/interruptibility), STT, LLM, TTS, Phoenix (real-time face) (https://docs.tavus.io/sections/conversational-video-interface/overview-cvi, accessed 2026-09-24).
- Objectives: goal-oriented steps with `auto`/`manual` confirmation, `output_variables`, and `next_conditional_objectives` / `next_required_objective` transitions (https://docs.tavus.io/sections/conversational-video-interface/persona/objectives, accessed 2026-09-24).
- Docs index pages for knowledge base (RAG documents), memories (per-participant continuity), perception (Raven), conversational flow tuning, recordings, webhooks, tools (LLM, perception, post-call, system), perception tools (fire when Raven sees/hears something), language support (https://docs.tavus.io/llms.txt, accessed 2026-09-24).
- Sparrow-0 (April 2, 2025): semantic/lexical endpoint detection, ~600 ms responses; Sparrow-2 referenced as the next iteration (https://www.tavus.io/blog/sparrow-0-advancing-conversational-responsiveness-in-video-agents-with-transformer-based-turn-taking, accessed 2026-09-24). Sparrow-2 details and the Guardrails page: UNVERIFIED (404 / snippet only).

---

## 8. Sesame

**(a) What renders beside the agent**
- May 27, 2026 post: iOS preview in 39 countries; agents work over voice and text; responses are enriched with a visual presentation including search cards with image results; Deep Dives; Notes; per-agent memory; Incognito Mode (https://www.sesame.com/blog/voice-your-curiosity, accessed 2026-09-24).
- Mobile preview page lists iOS and Android previews (https://www.sesame.com/mobile-preview, accessed 2026-09-24).

**(b) Product state / capabilities**
- Four characters: Simone and Charlie join Maya and Miles; parallel web searches; Android preview forthcoming; eyewear "coming 2027" (https://www.sesame.com/blog/voice-your-curiosity, accessed 2026-09-24; https://www.sesame.com/, accessed 2026-09-24).
- Background: $250M Series B and iOS beta on October 21, 2025; CSM speech generation (https://techcrunch.com/2025/10/21/sesame-the-conversational-ai-startup-from-oculus-founders-raises-250m-and-launches-beta/, accessed 2026-09-24).
- Reminders and post-call summaries: UNVERIFIED (search snippet only, not on the fetched post).

---

## 9. Intercom Fin Voice

**(a) What renders beside the agent**
- Fin Voice deploys via phone Workflows and call forwarding from Talkdesk, Zoom, Amazon Connect, Zendesk Talk, Aircall, CXone, Five9 (https://www.intercom.com/help/en/articles/10697275-deploy-fin-voice, accessed 2026-09-24). Anything Fin Voice renders inside the web Messenger: UNVERIFIED — both Messenger articles returned navigation only.
- Fin channels: chat, email, voice, Discord (plus WhatsApp/SMS references); handover to other support tools (https://www.intercom.com/help/en/articles/7120684-fin-ai-agent-explained, accessed 2026-09-24).

**(b) Advertised capabilities**
- Fin Voice 2 (Apex Flash model): 30-language support, full context on transfer, custom escalation rules, natural interruption handling, background noise reduction, automated follow-up SMS, sandbox preview, answer inspection, handoff simulation, simulated conversations (https://fin.ai/voice, accessed 2026-09-24).
- "AI Agent Disclosure" article exists under Fin settings (https://www.intercom.com/help/en/articles/11712008-ai-agent-disclosure, accessed 2026-09-24) — its content (label toggle, default intro text) UNVERIFIED.

---

## 10. Google Gemini Live / Live API / Project Astra

**(a) What renders in the Gemini Live app UI**
- Captions toggle, transcript reviewable after End, Hold (mic off), Mute, End, dedicated camera and screen-share buttons, interrupt-by-talking (toggleable), background/locked-phone continuation, connected apps (https://support.google.com/gemini/answer/15274899?hl=en&co=GENIE.Platform%3DAndroid, accessed 2026-09-24).
- Visual guidance: when sharing the camera, Gemini highlights items directly on screen (Aug 20, 2025) (https://blog.google/products-and-platforms/products/gemini/gemini-live-updates-august-2025/, accessed 2026-09-24).
- Guided Vision (Sept 1, 2026): spoken descriptions of the camera view, Android 9+ (https://blog.google/products-and-platforms/platforms/android/android-drop-september-2026/, accessed 2026-09-24).
- Aug 26, 2026: Spark outlines to Docs, Daily Brief, inbox actions, background multi-step tasks, personal memory; no on-screen card/status elements described (https://blog.google/innovation-and-ai/products/gemini-app/productivity-features-gemini-live/, accessed 2026-09-24).

**(b) Live API capabilities**
- Overview: affective dialog, proactive audio, interruptions, tool use + Google Search, input/output transcripts, 16 kHz PCM audio and ≤1 FPS JPEG (https://ai.google.dev/gemini-api/docs/live, accessed 2026-09-24).
- Capabilities guide: `enable_affective_dialog`, `proactive_audio`, `thinkingLevel` low/medium/high (3.8 Live Extended Thinking), `input_audio_transcription` / `output_audio_transcription`, VAD params (`startOfSpeechSensitivity`, `endOfSpeechSensitivity`, `silenceDurationMs`, `prefixPaddingMs`), interruption reported via `BidiGenerateContentServerContent`, async `NON_BLOCKING` function calling, models Gemini 3.8 Live / 3.8 Live Extended Thinking / 3.1 Flash Live Preview, 99 languages (https://ai.google.dev/gemini-api/docs/live-guide, accessed 2026-09-24).
- Session: 15-min audio-only / 2-min audio-video without compression; context window compression; ~10-min connection lifetime; `GoAway`; session resumption (https://ai.google.dev/gemini-api/docs/live-session, accessed 2026-09-24).
- Changelog Sept 15, 2026: Gemini 3.8 Live and Extended Thinking GA (https://ai.google.dev/gemini-api/docs/changelog, accessed 2026-09-24). Launch post: auto-detects and switches among 97 languages mid-conversation, near-real-time visual grounding, verbal acknowledgement cues (https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-live-gemini-3-8-live-extended-thinking/, accessed 2026-09-24). Note 97 vs 99 language counts across the two Google pages — reported as found.
- Project Astra: camera conversations, remembered preferences, proactive initiation, prototype glasses (https://deepmind.google/models/project-astra/, accessed 2026-09-24).
- "Gemini 3.5 Live Translate" and any visual "thinking" indicator in the app: UNVERIFIED.

---

## 11. ElevenLabs Agents (ElevenAgents)

**(a) What renders beside the agent**
- Widget: `expanded`/`full` variants, Chat Mode (text-only start), gradient orb, custom terms and conditions before the conversation, mute, multi-language selector, in-conversation feedback and end-of-call feedback prompt, brand colours, shareable landing page (https://elevenlabs.io/docs/agents-platform/customization/widget, accessed 2026-09-24).
- Client tools run in the browser/mobile app (https://elevenlabs.io/docs/agents-platform/customization/tools, accessed 2026-09-24).

**(b) Advertised capabilities**
- Overview: widget, RAG knowledge base, client tools, visual Workflows, SIP trunking, batch calling, automated tests, 5k+ voices in 31 languages, conversation evaluation (https://elevenlabs.io/docs/agents-platform/overview, accessed 2026-09-24).
- System tools: end call, language detection, agent transfer, transfer to number (human), skip turn, DTMF keypad tone, voicemail detection (optionally leave a message), update state (https://elevenlabs.io/docs/agents-platform/customization/tools/system-tools, accessed 2026-09-24).
- Workflows nodes: subagent, dispatch tool, agent transfer, transfer to number, end; edges with LLM conditions, expressions, backward loops (https://elevenlabs.io/docs/agents-platform/customization/agent-workflows, accessed 2026-09-24).
- Testing: simulation (multi-turn simulated user), next-reply scenario tests, tool-call tests; mock none/all/selected tools; create tests from real conversations; run via dashboard, CLI or API (https://elevenlabs.io/docs/eleven-agents/customization/agent-testing, accessed 2026-09-24).
- Analysis: custom success-evaluation criteria and structured data collection, surfaced in post-call webhooks, analytics, Spotlight, and transfer decisions (https://elevenlabs.io/docs/agents-platform/customization/agent-analysis, accessed 2026-09-24).
- Post-call webhooks: transcription, audio (base64 MP3), call-initiation-failure (https://elevenlabs.io/docs/agents-platform/workflows/post-call-webhooks, accessed 2026-09-24).
- WhatsApp: voice notes transcribed in, replies as voice notes or text, Meta templates for outbound, outbound calls need permission via template (https://elevenlabs.io/docs/eleven-agents/whatsapp, accessed 2026-09-24).
- Batch calling: CSV/XLS lists, per-recipient dynamic variables, scheduling, Twilio and SIP numbers (https://elevenlabs.io/docs/eleven-agents/phone-numbers/batch-calls, accessed 2026-09-24).

---

## 12. Briefly: Deepgram, Cartesia Line, Pipecat, Cresta/Parloa/PolyAI

- Deepgram Voice Agent API: client messages `Inject Agent Message`, `Update Prompt`, `Update Speak`; server events `Agent Thinking`, `Conversation Text`, `User Started Speaking`, function call request/response (https://developers.deepgram.com/docs/voice-agent-feature-overview, accessed 2026-09-24); product page: barge-in detection, turn-taking prediction, mid-session control, BYO LLM/TTS (https://deepgram.com/product/voice-agent-api, accessed 2026-09-24).
- Cartesia Line: code-first agents deployed to a managed runtime; call logs, custom-metric evaluations, developer control over interruptions/turn-taking (https://docs.cartesia.ai/line/introduction, accessed 2026-09-24).
- Pipecat RTVI standard events for client UIs: `bot-ready`, `client-ready`, `user-started/stopped-speaking`, `bot-started/stopped-speaking`, `user-transcription` (partial + final), `bot-output`, `bot-llm-text`, `bot-tts-text`, `llm-function-call`, `server-message`, `metrics` (https://docs.pipecat.ai/client/rtvi-standard, accessed 2026-09-24). Pipecat UI (shadcn registry): `Conversation`, `UserAudioControl`, mic/camera/screenshare/connection controls, live transcripts and visualizers (https://docs.pipecat.ai/client/voice-ui-kit, accessed 2026-09-24). Pipecat Flows: graph of nodes each scoped to one task and its tools; declarative vs programmatic flows; pre/post actions; visual editor at flows.pipecat.ai (https://docs.pipecat.ai/pipecat-flows/introduction, accessed 2026-09-24).
- Cresta: Agent Operations Center flags when to intervene, supervisors guide the next response or take over; escalation with shared memory; guardrails and supervisory models; synthetic-customer simulations; containment/escalation dashboards (https://cresta.com/ai-agent, accessed 2026-09-24). Voice-stack post mentions audio PII redaction (https://cresta.com/blog/understanding-crestas-voice-platform-the-voice-stack, accessed 2026-09-24).
- Parloa: real-time PII redaction before storage/model context, zero-retention, audit logs, 140+ languages (Aug 21, 2026) (https://www.parloa.com/knowledge-hub/pii-redaction-ai/, accessed 2026-09-24); thousands of simulated conversations across languages/channels (https://www.parloa.com/platform/test/, accessed 2026-09-24).
- PolyAI: handoff context via SIP headers, Handoff API, or Conversations API with identifiers, verification status, escalation reason, pre-handoff utterance, intent/slots (https://docs.poly.ai/call-handoff/introduction, accessed 2026-09-24); PCI Pal integration excludes card entry from recordings, masks DTMF, returns caller with payment confirmation (https://docs.poly.ai/integrations/pci-pal, accessed 2026-09-24).

---

## Could not verify

- Bland "Citrus" (no hits in 3 searches); Bland voice cloning (API page 404); Bland voice input in the web widget (fetched widget page and June 23, 2025 changelog do not mention it).
- Vapi A/B experiments; Vapi filler injection; Vapi HIPAA page content (not fetched).
- Retell SMS channel and outbound batch calls; Retell web-call test UI panels.
- Synthflow widget visuals, HIPAA/SOC 2, in-call SMS/WhatsApp.
- Hume Expression Measurement API specifics (48 dimensions, streaming) — three doc URLs 404'd; any plain "EVI 4".
- Tavus Guardrails page (404), Sparrow-2 details.
- Sesame reminders / post-call summaries.
- Intercom: Fin Voice rendering in Messenger; AI Agent label toggle default and intro message text.
- Gemini: "3.5 Live Translate", in-app visual thinking indicator, "opens inline".
- Pipecat voice-ui-kit component names beyond the two on the docs page; Deepgram filler-via-inject pattern.
- Cresta supervisor "whisper"/listen-in specifically (only guide/take-over verified); Parloa supervisor handover.

## Patterns that appear in 3+ products (fetched evidence only)

1. **Transcript beside the call** — OpenAI agents demo, Vapi widget/SDK, Retell widget (chat history), Gemini Live captions/transcript, Pipecat RTVI `user-transcription`/`bot-output`, Hume starter `Messages.tsx`.
2. **Web widget with both voice and text modes** — Vapi (Voice/Chat Mode), Retell (text, hybrid, callback), ElevenLabs (Chat Mode), Sesame (voice and text).
3. **Consent / AI-disclosure surface in the widget or config** — Vapi widget consent, ElevenLabs terms before conversation, Synthflow greeting/consent messages, Intercom disclosure article.
4. **Warm transfer with whisper or AI summary to the human, plus human/queue detection** — Vapi, Retell, Synthflow, Bland; PolyAI passes structured context instead.
5. **Voicemail detection with optional message** — Vapi, Retell, ElevenLabs, Synthflow, Bland (AMD in warm transfer).
6. **AI-caller simulation testing with pass/fail criteria and tool mocking** — Vapi, Retell, ElevenLabs, Cresta, Parloa, Intercom (simulated conversations).
7. **PII scrubbing / storage tiers** — Vapi `compliancePlan`, Retell storage tiers, Parloa real-time redaction, PolyAI PCI Pal, Cresta audio redaction.
8. **Cross-channel memory** — Bland (voice/SMS/iMessage/chat), Sesame (per-agent memory), Tavus memories, Gemini personal memory, Project Astra.
9. **Tool/function-call activity exposed to the client** — OpenAI breadcrumbs + ToolPanel, Pipecat `llm-function-call`, Tavus tool-call and perception events, Deepgram function-call events, Vapi `message` events.
10. **Speaking-state events for status indicators** — Pipecat RTVI, Vapi `speech-start/end`, Deepgram `User Started Speaking`, Tavus started/stopped speaking, Gemini interruption signal.
11. **Screen/camera share feeding the agent** — Gemini Live, Tavus (`ScreenShareButton`), Pipecat UI controls, Project Astra.
12. **Structured post-call extraction (custom fields)** — Retell (Text/Selector/Boolean/Number), ElevenLabs data collection, Vapi `artifactPlan`/`analysisPlan`, Tavus objective `output_variables`, Bland pathway variables.