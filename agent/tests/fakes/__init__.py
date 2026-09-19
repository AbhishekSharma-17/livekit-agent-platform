"""In-memory fakes for offline `lkap_agent` tests (no LiveKit connection, no network).

- `fake_room`: duck-typed `rtc.Room`/`LocalParticipant`/`RemoteParticipant` that
  record every `send_text`/`stream_bytes`/`perform_rpc`/`register_rpc_method`
  call and can emit synthetic track-subscription events.
- `fake_ctx`: `FakePackSessionContext`, an in-memory implementation of every
  `packs.base` Protocol (`UiChannel`, `FrameBufferProto`, `KbClient`,
  `StructuredLLM`, `ImageGen`, `BackgroundRunner`), for pack/tool unit tests.

Wave-1/2 packages (W1-AGENT-UI, W1-AGENT-TOOLS, W2-PACK-INSURANCE-TOOLS) add
their own fakes alongside these (`fake_llm.py`, `fake_stt.py`, ...); this
package only owns the two files above.
"""
