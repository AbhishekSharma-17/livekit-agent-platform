# Running One Agent Platform Against Multiple LiveKit Deployments

Research date: 2026-09-19. Sources verified: `docs.livekit.io`, `github.com/livekit/livekit`, `github.com/livekit/agents` (installed 1.8.2 + `main` via GitHub), `github.com/livekit/livekit-cli`, the `livekit-api` Python SDK, and the installed venv at
`.../livekit_agent_platform/agent/.venv/lib/python3.12/site-packages/livekit/` (versions: `livekit-agents` 1.8.2, `livekit-api` 1.2.1, `livekit-protocol` 1.1.27, `livekit` (rtc) 1.1.18).

Every claim below is tagged with its source. Anything I could not verify against a primary source is marked **UNVERIFIED**.

---

## 1. Worker ↔ server binding

### 1.1 One worker process = one URL, one API key/secret, one agent

`AgentServer.__init__` binds exactly one deployment at construction time:

```python
self._ws_url = ws_url or os.environ.get("LIVEKIT_URL") or ""
self._api_key = api_key or os.environ.get("LIVEKIT_API_KEY") or ""
self._api_secret = api_secret or os.environ.get("LIVEKIT_API_SECRET") or ""
```
Source: `agent/.venv/.../livekit/agents/worker.py:334-336`.

`AgentServer.run()` refuses to start without all three:
```python
if not self._ws_url:
    raise ValueError("ws_url is required, or set LIVEKIT_URL environment variable")
if not self._api_key: raise ValueError("api_key is required, ...")
if not self._api_secret: raise ValueError("api_secret is required, ...")
```
Source: `worker.py:679-690`. There is exactly one `ws_url`/`api_key`/`api_secret` triple per `AgentServer` instance — a worker process cannot register with two LiveKit servers/projects simultaneously. `AgentServer.update_options()` (worker.py:857-902) lets you *change* these values before `run()` (it raises `RuntimeError` if called after the server has started, `worker.py:875`), but not run against two at once.

### 1.2 One `rtc_session` (one agent_name / one entrypoint) per `AgentServer`

```python
raise ValueError(
    "The AgentServer currently only supports registering only one rtc_session"
)
```
Source: `worker.py:505` (inside the `rtc_session` decorator/method, `worker.py:451-521`). The docstring literally says "currently only one rtc_session" (`worker.py:371`), i.e. this is a known, explicit single-entrypoint limitation, not an incidental one. Verified unchanged on `main` by fetching `https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/worker.py` directly (2026-09-19): the same guard string appears at line 529, and the same `ws_url` binding (`self._ws_url = ws_url or os.environ.get("LIVEKIT_URL") or ""`) appears at line 354 — line numbers differ slightly from the installed 1.8.2 but the logic is identical.

**Conclusion for §1's core question:** No — one worker process cannot register with more than one LiveKit server/project, and it cannot host more than one `agent_name` entrypoint. Both constraints are structural (one `ws_url`, one `rtc_session` per `AgentServer` object), confirmed against the class you already use (`AgentServer` + `@server.rtc_session(agent_name=...)`).

### 1.3 Multiple `AgentServer` instances in one process — possible but not the supported pattern

Nothing in `worker.py` prevents instantiating two `AgentServer` objects with different `ws_url`/`agent_name` in the same Python process and calling `.run()` on each concurrently via `asyncio.gather`. However:

- Each `AgentServer.run()` stands up its own health-check HTTP server on `self._host`/port (`ServerEnvOption.getvalue(self._port, devmode)`, `worker.py:645-671`). The port default is `ServerEnvOption(dev_default=0, prod_default=8081)` (`worker.py:249,302`) — `0` means the OS auto-assigns a free port in dev mode, but **in production mode the default is a fixed `8081` for every instance**, so two `AgentServer`s in one process running with `devmode=False` will port-collide unless you explicitly pass a distinct `port=` to each constructor.
- Each instance spins up its own `ipc.proc_pool.ProcPool` (a full process pool for job execution) and, if you use `_InferenceRunner`s (e.g. local VAD), its own `InferenceProcExecutor` (`worker.py:598-615`) — so N `AgentServer`s in one process means N job-process pools and N inference subprocess trees, which defeats most of the resource-sharing benefit of "one process."
- The CLI entrypoint (`python -m livekit.agents start`) discovers exactly **one** `AgentServer` per module via `_discover_server` (`agents/__main__.py:82-91`, "the single AgentServer in the module"), so this pattern only works if you drive `.run()` yourself and skip the `lk`/`python -m livekit.agents` CLI runners (`_run_worker`/`_run_tcp_console` in `agents/cli/cli.py`, referenced from `agents/__main__.py:59-73`).

**Conclusion:** technically constructible, not documented or supported as a pattern, and it multiplies OS-level resources (ports, process pools, inference subprocesses) per connection rather than sharing them — the opposite of a "safe" multiplexing strategy. UNVERIFIED beyond what the source shows: whether LiveKit has ever explicitly tested/blessed this; I found no doc or comment endorsing it.

### 1.4 Recommended pattern: one worker process per connection, supervised

Given 1.1–1.3, the supported multi-deployment pattern is **one `AgentServer` process per `(LiveKitConnection, agent_name)` pair**, each with its own `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` (or constructor args) and its own health-check port. A supervisor (systemd, a container orchestrator, or a small Python process manager in your FastAPI app) starts/stops one such process per stored `LiveKitConnection` row. This matches how the CLI itself expects to be invoked (`python -m livekit.agents start` discovers one `AgentServer`; you'd invoke it once per connection with different env vars / `--url --api-key --api-secret` flags — see `agents/__main__.py:100-104` for those CLI flags).

### 1.5 How workers load-balance and how dispatch finds them

- On connect, the worker sends a `RegisterWorkerRequest` including `agent_name`, `type` (JT_ROOM/JT_PUBLISHER), `allowed_permissions`, `version`, and (if hosted on Cloud) `deployment` (`worker.py:1118-1134`). The server assigns jobs to registered workers matching the room's requested `agent_name` (implicit dispatch: any worker with `agent_name=""`; explicit dispatch: only workers whose `agent_name` matches the dispatch's requested name — see §1.6).
- Worker load is reported via periodic `UpdateWorkerStatus` messages using `_worker_load` computed from `load_fnc`/`load_threshold` (`worker.py:1320-1330` area; default `load_fnc` = CPU moving average via `_DefaultLoadCalc`, `worker.py:80-108`). Multiple workers registered under the **same** `agent_name` against the **same** LiveKit server/project form a load-balanced pool. **UNVERIFIED (server-side, not opened in this research):** the exact selection algorithm (e.g. strictly least-loaded vs round-robin among available workers) lives in `github.com/livekit/livekit`'s server code, which this session did not open — treat "picks an available, under-threshold worker of that pool" as the verified claim, and "least-loaded specifically" as an unconfirmed refinement. Either way this is standard single-deployment horizontal scaling — it does not span two different LiveKit servers, because each worker's registration is a single WebSocket connection to one `ws_url` (§1.1).
- **Multi-deployment load balancing across two different LiveKit servers does not exist** as a platform feature — each server tracks and load-balances only the workers connected to *it*. To "load balance" across your own Cloud project A and self-hosted server B you must run independent worker pools per deployment (§1.4) and let your own token-minting logic decide which connection a given session should use.
- Explicit dispatch: setting `agent_name` on the `AgentServer`/`WorkerOptions` disables automatic dispatch to that name; jobs are created only via `AgentDispatchService.create_dispatch` (server API) or by embedding the agent name in the token's `RoomConfiguration.agents` (comment: "Set agent_name to enable explicit dispatch... jobs will not be dispatched to rooms automatically", `worker.py:220-227`). This matches your platform's current explicit-dispatch design.

### 1.6 Credential rotation

- The worker only reads `ws_url`/`api_key`/`api_secret` at construction (or via `update_options()` before `.run()`); there is no live "hot-rotate the connected server's credentials" API on a running, already-registered `AgentServer`. `update_options()` explicitly refuses to run once started — `if not self._closed: raise RuntimeError("cannot update options after starting the server")` (`worker.py:857,875`) — so it is pre-run configuration only, not a runtime credential swap.
- If you rotate a `LiveKitConnection`'s API key/secret in your DB, the running worker process for that connection keeps using its original in-memory secret until restarted — you must restart/re-supervise that connection's worker process(es) after a credential rotation. This is exactly the same operational requirement as rotating `LIVEKIT_API_KEY`/`SECRET` today for your single deployment, just per-connection instead of global.
- One related runtime detail: `_reload_jobs` decodes/re-signs a participant token with the current `self._api_secret` when reloading a job (`worker.py:1253-1274`) — another confirmation that the secret is fixed for the life of the process.
- Server-side, LiveKit API keys/secrets are configured in the server's `keys:` config block (self-host) or issued per Cloud project; rotating them there immediately invalidates tokens signed with the old secret and disconnects/rejects any worker still presenting the old one on next reconnect attempt (standard JWT HS256 behavior in `AccessToken`/`TokenVerifier`, `api/access_token.py`).

---

## 2. Self-hosted LiveKit

### 2.1 Requirements (Source: `docs.livekit.io/home/self-hosting/deployment/`, fetched 2026-09-19)

- Config fields: `port: 7880` (signaling/HTTP), `rtc.tcp_port: 7881`, `rtc.port_range_start/end: 50000-60000` (UDP media), `use_external_ip: true` for cloud VM deployments, `redis.address` for production, `keys:` (API key/secret pairs).
- **TLS**: a real domain + CA-signed certificate is required for the WebSocket endpoint (e.g. `wss://livekit.yourhost.com`); "self-signed certs do not work here" (doc's own wording).
- **TURN**: optional but recommended for clients behind restrictive firewalls/NATs; TURN/TLS needs its own domain+cert (`tls_port`, must be 443 without a load balancer, LiveKit does its own TLS termination); TURN/UDP variant can share port 443 for firewall compatibility.
- **Redis**: "recommended for production deploys" — the doc doesn't phrase it as a hard requirement for single-node, but it is required to run more than one `livekit-server` node behind a load balancer (multi-node coordination). UNVERIFIED: exact single-node-without-Redis limitations weren't spelled out on the fetched page; treat Redis as mandatory for any HA/multi-node self-host.

### 2.2 Component topology for self-host (Source: `docs.livekit.io/transport/self-hosting/`, fetched 2026-09-19)

Self-hosting means you run, and are responsible for, each of these separately:
- `livekit-server` (SFU/media + signaling) — github.com/livekit/livekit
- Redis (multi-node coordination)
- **Egress** service — "deployed as a separate service"
- **Ingress** service — "deployed as a separate service"
- **SIP** service — "deployed as a separate service" (also confirmed at `docs.livekit.io/sip/`: "If you're self hosting LiveKit, the SIP service needs to be deployed separately")

None of these ship bundled with `livekit-server`; each is its own container/binary from `github.com/livekit/egress`, `github.com/livekit/ingress`, `github.com/livekit/sip` respectively (repo names UNVERIFIED beyond the doc's "deployed as a separate service" language — I did not open those three repos individually).

### 2.3 Cloud-only features (Source: `docs.livekit.io/transport/self-hosting/`, `docs.livekit.io/home/cloud/`, `docs.livekit.io/transport/media/noise-cancellation/`, `github.com/livekit/agents/blob/main/examples/homepage/knowledge_base/products/livekit-inference.md`, `docs.livekit.io/deploy/observability/data/`, fetched 2026-09-19)

| Feature | Cloud | Self-host |
|---|---|---|
| **LiveKit Inference** (unified STT/LLM/TTS API, no per-vendor keys) | Yes | **No.** Doc, verbatim: *"LiveKit Inference is a LiveKit Cloud feature only... If you self-host LiveKit, you must use model plugins instead and manage your own API keys and accounts with each provider."* Nuance confirmed by a second source: Inference is available to *any agent that uses LiveKit Cloud for **transport***, even if the agent process itself is self-run — it's gated on the LiveKit *server/project* being Cloud, not on where the worker process runs. |
| **Enhanced noise cancellation — Krisp NC** (`livekit-plugins-noise-cancellation`, background noise suppression) | Included with Cloud | **No** — plugin requires a LiveKit Cloud project; not viable self-hosted per LiveKit community guidance (secondary source, cross-checked against the plugin's own Cloud-auth requirement). |
| **Krisp BVC / VIVA (voice isolation)** and **ai-coustics Voice Focus (QUAIL_VF_S/L)** | Cloud, additional cost from 2026-05-01 | No, unless ai-coustics is used with your **own** license key: *"By default the ai-coustics plugin authenticates and meters usage through LiveKit Cloud. If you self-host your SFU instead of using LiveKit Cloud, you can authenticate directly against ai-coustics by passing your own license key."* So ai-coustics NC can work self-hosted with a direct vendor key; Krisp models cannot. |
| **`lk agent create` / `lk agent deploy` (managed container hosting)** | Yes — builds and runs your agent container on Cloud infra | **No** — no self-host equivalent; you build/run/orchestrate your own worker containers (see §3). |
| **Agent Observability** (dashboard: synced audio playback, transcripts, turn-by-turn traces, logs) | Yes, beta, per-project toggle under Data & Privacy | **No dashboard.** But: SDK-side `AgentSession`/session-report primitives (`make_session_report()`, `.to_dict()`) "run entirely in your agent process from data already collected by the SDK. They don't make requests to LiveKit Cloud, so the same code works for self-hosted deployments" — i.e. you can still collect the same data yourself and ship it to your own store; you just don't get LiveKit's hosted UI. |
| **Hosted turn detector (`inference.TurnDetector`, `version="v1"`)** | Yes, via `LIVEKIT_INFERENCE_URL`/`LIVEKIT_INFERENCE_API_KEY`/`SECRET` | Falls back automatically to a **local** model, `turn-detector-v1-mini`, when those env vars are absent or the cloud call fails (see §2.4) — this local fallback runs anywhere, self-host included. |
| **Development token server** (quick test tokens from the dashboard) | Yes — replaces the now-removed "LiveKit Sandbox" | Not applicable in the same form; self-host has no dashboard, so you mint tokens yourself (which your FastAPI backend already does). Note: **"LiveKit Sandbox is no longer available in LiveKit Cloud"** as of the fetched docs — the task's framing of "Cloud sandbox token servers" is stale; the current Cloud equivalents are Agent Console (agent testing) and the Development token server (frontend testing). |
| Global mesh SFU / nearest-edge routing / no per-room size cap (self-host caps near ~3,000/room) / built-in Analytics API / access-token revocation / 99.99% SLA / native LiveKit Phone Numbers / SIP-trunk-level noise cancellation | Cloud-only per `docs.livekit.io/home/cloud/` | Not available; self-host is single-home per SFU node (though Redis + multiple nodes gives you your own multi-node scaling, not LiveKit's global mesh). |
| Webhooks (room/participant/track/egress/ingress events), SIP core APIs, Egress, Ingress, basic realtime media, E2EE | Yes | **Yes** — functionally identical once you deploy the separate services (§2.2); see §6 for detail. |

### 2.4 Turn detector — hosted vs local fallback (Source: `agent/.venv/.../livekit/agents/inference/eot/detector.py`, installed 1.8.2)

`inference.TurnDetector(version=...)`:
- If `version` is left unset, it auto-resolves to `"v1"` (hosted) when `utils.is_hosted()` or dev mode, else `"v1-mini"` (local) — but this initial guess is then corrected by credential availability.
- For `version="v1"`, it requires `LIVEKIT_INFERENCE_URL`, and an API key/secret resolved from `LIVEKIT_INFERENCE_API_KEY`/`LIVEKIT_API_KEY` and `LIVEKIT_INFERENCE_API_SECRET`/`LIVEKIT_API_SECRET`. If any are missing **and** the version was auto-resolved, it logs a warning and silently downgrades to the local `turn-detector-v1-mini` model (~108MB, resident for the process lifetime). If the caller explicitly asked for `version="v1"` and credentials are missing, it raises `ValueError` instead of silently downgrading.
- At runtime, if the cloud (`v1`) transport fails mid-stream, `_fallback_to_local()` swaps in the local `_LocalTransport` and the swap is **one-way/sticky** — it never re-attempts cloud for the rest of that stream (`base.py`, `_fallback_to_local`/`_run`). If `local_fallback=False`, a cloud failure instead calls `_degrade()`, which turns off turn detection entirely for the rest of the stream (turns then commit purely on the endpointing-delay timer).
- **Design impact for your platform, more precisely than "quietly falls back":** the auto-resolution in `detector.py` picks `"v1"` (hosted) whenever `utils.is_hosted()` **or** `utils.is_dev_mode()` is true, else `"v1-mini"` (local) directly — no cloud attempt at all (`agents/utils/misc.py`: `is_hosted()` returns `os.getenv("LIVEKIT_REMOTE_EOT_URL") is not None`; `is_dev_mode()` returns `os.getenv("LIVEKIT_DEV_MODE") == "1"`, true only under `lk`'s `console`/`dev` runners). Also note `get_default_inference_url()` (`agents/inference/_utils.py`) **always returns a non-empty URL** — LiveKit's production Inference gateway by default, or the staging gateway if `LIVEKIT_URL` contains `.staging.livekit.cloud` — it is never simply absent. So the real-world behavior per connection is:
  - **Self-hosted connection, running as a normal production worker container** (not `is_hosted()`, not `is_dev_mode()`): auto-resolves straight to `"v1-mini"` and never contacts any Inference gateway. This is the quiet, no-network-dependency case.
  - **Self-hosted connection, run via `lk agent dev`/`console` locally** (`is_dev_mode()` true): auto-resolves to `"v1"` and *will* attempt the (always-non-empty) default Inference gateway URL using that connection's `LIVEKIT_API_KEY`/`SECRET` — which are self-hosted-server credentials, not valid against LiveKit's Cloud Inference gateway — so this attempt fails auth, logs `"LIVEKIT_INFERENCE_URL is set but ... missing"`-style or a runtime cloud-transport-failure warning, and falls back to `"v1-mini"` mid-stream. Not silent; it is a visible warning plus one failed round-trip per session.
  - Recommend explicitly passing `version="v1-mini"` for any non-Cloud-Inference connection to skip this dev-mode failed-attempt path entirely, rather than relying on auto-resolution.

### 2.5 Direct impact on your design

- Every `LiveKitConnection` that is **not** LiveKit Cloud (or is Cloud but you choose not to configure `LIVEKIT_INFERENCE_*` for it) must supply its own STT/LLM/TTS vendor keys through your existing per-provider key storage — LiveKit Inference cannot be assumed available. This should be a capability flag (see §8).
- Krisp-based noise cancellation plugins should not be offered as an option for self-hosted connections; ai-coustics can be offered self-hosted only if you're willing to hold a separate ai-coustics license key per self-hosted connection.
- `lk agent deploy`-based hosting is Cloud-only — for self-hosted connections (and for Cloud connections where you don't want Cloud to run your container), your own worker-container fleet is the only option (§3).
- Turn detection degrades gracefully by itself; no blocking dependency, but expose the connection's Inference availability so the UI can explain why turn detection is "basic" on that connection.

---

## 3. Deploying agents

### 3.1 LiveKit Cloud agent hosting (Source: `github.com/livekit/livekit-cli` `cmd/lk/agent.go`, `cmd/lk/project.go`, `pkg/config/config.go`, fetched 2026-09-19; `docs.livekit.io/deploy/agents/builds/`, `docs.livekit.io/deploy/admin/quotas-and-limits/`)

- `lk agent create` / `lk agent deploy` build a container from your project directory (Dockerfile auto-generated if absent), upload it (**1 GB max build-context size**, **10–15 minute build timeout** — docs say 10 min, the `agent.go` source constant `buildTimeout = 15 * time.Minute`; treat as ~10–15 min), and run it as a managed, autoscaled worker fleet on LiveKit Cloud infrastructure.
- A `livekit.toml` in the project directory records the project's **subdomain** and agent id; `agent.go` explicitly cross-validates the currently-selected CLI project's URL subdomain against `livekit.toml`'s recorded subdomain and errors ("project does not match agent subdomain") if you point a different project at an existing `livekit.toml`.
- Secrets: `--secrets` flag at deploy time, `lk agent update-secrets` afterward, injected as env vars into the managed container (Cloud secrets manager — not your local `.env`).
- Regions: Cloud lets you pick agent deployment regions (US East / EU Central / AP South per search result — **UNVERIFIED** exact list against a primary doc page; I found this via secondary search summary, not a direct fetch of a regions doc page).
- Per-project quotas that bound this (Source: `docs.livekit.io/deploy/admin/quotas-and-limits/`, Build/free tier numbers, paraphrased): concurrent agent sessions (5 on Build), build context 1 GB, cold start 10–20s on the free tier, plus general WebRTC/inference/observability quotas (participants, egress/ingress concurrency, STT/TTS concurrent connections, LLM RPM/TPM, observability event/audio-upload rate, 30-day retention, server API 1000 req/min). Self-hosted has **no equivalent published quota system** — you own the capacity planning entirely (not explicitly stated as "unlimited," just that this Cloud-specific page doesn't apply).

### 3.2 Running your own worker containers against any server

This is exactly the pattern in §1.4: build your own Docker image around your existing `AgentServer` code (same `entrypoint.py`), and run N copies of it (one per `LiveKitConnection`, or several for load-balanced scale-out on one connection) with `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` (or `--url/--api-key/--api-secret` CLI flags — `agents/__main__.py:100-104`) set per container from that connection's stored, decrypted credentials. This works identically against LiveKit Cloud or self-hosted `livekit-server` — the SDK does not care (confirmed: `AgentServer` only inspects `ws_url` for its own environment propagation, `worker.py:720-722`; nothing in the connect/register path branches on Cloud vs self-host except the `cloud_agents` flag).

That flag is `WorkerInfo.cloud_agents`, computed as `cloud_agents=bool(self._worker_token)` (`worker.py:534`) — `self._worker_token` is populated only when the process is launched under LiveKit Cloud's managed agent-hosting runtime (i.e. via `lk agent deploy`, which injects a `LIVEKIT_WORKER_TOKEN`), **not** merely by pointing `LIVEKIT_URL` at a Cloud project. Practically: your own containers, even when connected to a LiveKit Cloud project, are never "cloud_agents" — they keep full control over `load_fnc`/`load_threshold`. Only agents Cloud itself builds and runs (§3.1) have those overridden back to the SDK default (`worker.py:580-590`: *"custom load_fnc is not supported when hosting on Cloud, reverting to default"*).

### 3.3 Can `lk agent deploy` target a project chosen at runtime from stored credentials?

Yes, mechanically: `lk project add --url ... --api-key ... --api-secret ... <name>` persists a `ProjectConfig{Name, ProjectId, URL, APIKey, APISecret}` in the CLI's YAML config (`pkg/config/config.go`), and `lk agent deploy` resolves "the project" either from `--project <name>` or interactive selection, then does `cloudagents.New(cloudagents.WithProject(project.URL, project.APIKey, project.APISecret))` (`cmd/lk/agent.go:553`, `:678`) — i.e. it is driven purely by whichever URL/key/secret triple you hand it, exactly like your own stored `LiveKitConnection` rows. **But** this only works end-to-end if that project is a LiveKit Cloud project with agent-hosting enabled — pointing `lk agent deploy` at a self-hosted server's URL/key/secret will fail at the Cloud-hosting API call (self-hosted `livekit-server` has no `cloudagents` build/run service to talk to). So: "deploy to connection X" via `lk agent deploy` is only meaningful when connection X's `deployment_type == cloud`.

### 3.4 What a "Deploy to connection X" button should actually do, per case

- **Connection X = LiveKit Cloud, want Cloud-hosted agent**: shell out to (or re-implement the underlying Twirp calls of) `lk agent deploy --project <ephemeral-config-pointing-at-X>` — practically, this likely means writing a throwaway `lk` CLI config profile (or directly using the `cloudagents` Go client's HTTP API — **UNVERIFIED**: I did not find a public Python equivalent of `cloudagents.Client`; it currently appears to be a Go-only package under `livekit/server-sdk-go/v2/pkg/cloudagents`) with X's URL/key/secret, then building/pushing your agent image.
- **Connection X = LiveKit Cloud or self-hosted, want your own worker fleet**: the button should provision/restart a container (in your own orchestrator — Docker/K8s/systemd) with env vars `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_AGENT_NAME` set from the decrypted `LiveKitConnection` row, running your existing agent image. This is the universal, always-available path (§3.2) and should probably be the default/only implementation for v1, since it works for both Cloud and self-host without depending on the (Go-only, harder to embed) Cloud agent-hosting API.
- **Connection X = self-hosted**: only the "your own worker fleet" option applies; the UI should not offer "Cloud-hosted deploy" for this connection at all (capability flag `cloud_hosting=false`, §8).

---

## 4. Browser side

- Token minting is per-connection: your FastAPI backend already mints `AccessToken(api_key, api_secret)` JWTs (`api/access_token.py`) — nothing in the SDK ties a token to a specific server URL; the URL the browser connects to is a value **you** hand the frontend alongside the token, not something embedded in or derivable from the JWT itself (the JWT has no `iss`-as-URL semantics beyond `iss = api_key`).
- **`livekit-client`'s connect API requires an explicit server URL on every call**, confirmed directly against the installed package in this repo's frontend (`web/node_modules/.pnpm/livekit-client@2.22.3.../node_modules/livekit-client/dist/src/room/Room.d.ts`): `Room.connect(url: string, token: string, opts?: RoomConnectOptions): Promise<void>`. There is no global/singleton server URL anywhere in the client SDK's public API — every `Room` instance takes its own `url` argument.
- **This codebase already does the right thing.** `web/src/lib/livekit.ts` defines `createConnectTokenSource`, whose `TokenSource.custom(...)` callback calls the backend's `/connect` endpoint (`fetchConnect`) and returns `{ serverUrl: details.serverUrl, participantToken: details.participantToken }` per session — i.e. the server URL is already threaded through from the backend's connect response on every session, not read from a build-time env var or hardcoded constant. `web/src/contracts/lkap-contracts.d.ts:315` likewise types `serverUrl: string` as part of the connect response contract, and `web/tests/livekit-connect.test.ts` exercises this with a fake URL (`"wss://example.livekit.cloud"`), confirming the URL is treated as request-scoped data, not a fixture/global. **No global-client-URL assumption found in this frontend** — extending it to multiple `LiveKitConnection`s is a backend-only change (have `/connect` look up and return the right connection's `url` alongside a token signed with that connection's `api_secret`); no frontend code changes are required for URL-handling itself.

---

## 5. Validating stored credentials from the UI

Cheap, low-side-effect calls to prove a `(url, api_key, api_secret)` triple is valid, using `livekit.api.LiveKitAPI` (installed `livekit-api` 1.2.1):

```python
from livekit import api

lkapi = api.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
try:
    rooms = await lkapi.room.list_rooms(api.ListRoomsRequest())
except api.TwirpError as e:  # alias of ServerError, api/twirp_client.py
    ...
```

- **`RoomService.list_rooms`** (`api/room_service.py`) — cheapest, read-only, requires only `room_list=True` grant (auto-applied by the SDK method). Works on both Cloud and self-host (RoomService is core, always present). **Recommended default "test connection" call.**
- **`AgentDispatchService.list_dispatch(room_name)`** (`api/agent_dispatch_service.py`) — requires a `room_name` argument (it's scoped to a room, not global), so it's a poor "does this credential work at all" probe. **UNVERIFIED**: whether passing a nonexistent room name returns an empty list vs a `not_found` error — I did not exercise this against a live server, only read the client code, which just serializes a `ListAgentDispatchRequest(room=room_name)` and returns whatever the server responds with (`agent_dispatch_service.py`, `list_dispatch`). Treat it as a secondary check to confirm the credential carries `room_admin` rights (which `create_dispatch`/`list_dispatch` require via `VideoGrants(room_admin=True, ...)`), not as the primary validity probe.
- **SIP list calls** — `SipService.list_inbound_trunk` / `list_outbound_trunk` / `list_dispatch_rule` (`api/sip_service.py`) — useful specifically to confirm SIP is reachable on that deployment (self-hosted SIP is a separate service at a potentially different internal address than the main `livekit-server`, per §2.2/§6.1); a failure here doesn't necessarily mean the *main* credentials are bad, just that SIP isn't deployed/reachable — so don't use this as your primary "are these creds valid" check, use it as a `sip_enabled` capability probe (§8).
- **Errors on bad credentials** (Source: `api/twirp_client.py`, `ServerError`/`ServerErrorCode`):
  - Wrong `api_secret` (signature won't verify server-side) or wrong `api_key` (server doesn't recognize the issuer) → the Twirp call returns a 4xx, surfaced as `ServerError` with `code` typically `"unauthenticated"` or `"permission_denied"` (`ServerErrorCode.UNAUTHENTICATED` / `.PERMISSION_DENIED`, `twirp_client.py`) and `.status` in the 401/403 range. `TwirpClient.request()` treats any `resp.status < 500` as terminal (no retry/failover) — so a bad-credential error returns immediately rather than being masked by the region-failover logic (`twirp_client.py:230-236`).
  - Wrong `url` (unreachable host, wrong port, DNS failure, non-LiveKit server) → `aiohttp.ClientError`/`asyncio.TimeoutError` is caught inside `TwirpClient.request`; on Cloud URLs this triggers the region-failover retry loop first (`_REGION_CACHE.region_origins`, `_failover.py`) before ultimately re-raising the transport exception if no failover region is discoverable — so a bad self-hosted URL surfaces as a raw `aiohttp` connection error (after a short failover-discovery delay), while a valid-format-but-wrong Cloud subdomain may take slightly longer due to the failover attempt. Recommend a short explicit `timeout=` when validating from the UI so a bad self-hosted URL doesn't hang the request for the SDK's full retry/backoff budget.
  - Malformed/garbage URL (not even a valid `ws(s)://`/`http(s)://`) → fails in `TwirpClient.__init__`'s `urlparse` handling or in `aiohttp`'s URL validation before any network call.

---

## 6. Related server features per connection

| Feature | API surface (`livekit-api` module) | Self-host | Cloud |
|---|---|---|---|
| **SIP inbound/outbound trunks, dispatch rules, `CreateSIPParticipant`** | `SipService` (`api/sip_service.py`): `create_inbound_trunk`, `create_outbound_trunk`, `create_dispatch_rule`, `create_sip_participant` (outbound calls; takes `wait_until_answered`, dial-timeout handling via `_dial_timeout.py`), `transfer_sip_participant` | Yes, but **the SIP service must be deployed separately** from `livekit-server` (`docs.livekit.io/sip/`) | Yes, "ready to use... without any additional configuration" (same doc) |
| **Egress** (room/track recording to S3/GCS, streaming) | `EgressService` (`api/egress_service.py`): `start_room_composite_egress`, `start_track_egress`, `start_participant_egress`, `list_egress`, `stop_egress`, `update_layout`/`update_stream` | Yes, **separate service** (§2.2) | Yes, subject to quota (2 concurrent egress on Build tier, 3h/12h duration caps per §3.1) |
| **Ingress** (bring external RTMP/WHIP streams into a room) | `IngressService` (`api/ingress_service.py`): `create_ingress`, `update_ingress`, `list_ingress`, `delete_ingress` | Yes, **separate service** (§2.2) | Yes, subject to quota (2 concurrent per §3.1) |
| **Webhooks** (room/participant/track/egress/ingress events) | `WebhookReceiver.receive(body, auth_token)` (`api/webhook.py`) — verifies via `TokenVerifier`, checks `sha256` claim against `hashlib.sha256(body).digest()` (base64-compared) | Yes — configured in `livekit-server`'s config file `webhook:` section (API key + target URLs) | Yes — configured via Cloud dashboard (Settings → Webhooks, choose signing key) |
| **Per-project limits** | N/A (platform-level, not an API call) | No published/enforced quota system found in docs — you plan capacity yourself | Yes — see §3.1 table (participants, media subscriptions, egress/ingress concurrency, STT/TTS concurrency, LLM RPM/TPM, agent sessions, observability event/audio rate, server API RPM), tiered by plan (Build/Ship/Scale/Enterprise) |

Webhook verification code, exactly as shipped (`api/webhook.py`):
```python
class WebhookReceiver:
    def __init__(self, token_verifier: TokenVerifier):
        self._verifier = token_verifier

    def receive(self, body: str, auth_token: str) -> WebhookEvent:
        claims = self._verifier.verify(auth_token)
        if claims.sha256 is None:
            raise Exception("sha256 was not found in the token")
        body_hash = hashlib.sha256(body.encode()).digest()
        claims_hash = base64.b64decode(claims.sha256)
        if body_hash != claims_hash:
            raise Exception("hash mismatch")
        return Parse(body, WebhookEvent(), ignore_unknown_fields=True)
```
This means **per-connection webhook verification needs that connection's own `api_key`/`api_secret`** passed into `TokenVerifier(api_key, api_secret)` — if you receive webhooks from multiple `LiveKitConnection`s at one endpoint, you must look up which connection a webhook belongs to (e.g. by a path segment or query param you configure per-connection when registering the webhook URL) *before* you know which secret to verify it with, since the payload's own claims don't self-identify the source project until after successful verification.

---

## 7. Security

- **Storing API secrets per connection**: same pattern you already use for provider keys — Fernet-encrypt each `LiveKitConnection.api_secret` (and `api_key`, defense-in-depth) at rest, decrypt only in-process when constructing an `AccessToken`/`LiveKitAPI`/spawning a worker; never log the decrypted value (the SDK itself is careful about this — `field(repr=False)` on `AgentServer`'s `api_key`/`api_secret` fields, `worker.py:232-236`, so they don't leak via `repr()`/default dataclass logging).
- **Least privilege**: `VideoGrants`/`SIPGrants`/`InferenceGrants`/`ObservabilityGrants` (`api/access_token.py`) are fine-grained — mint the narrowest grant needed per purpose:
  - Server-to-server validation calls (§5) only need `room_list=True` (auto-set by `list_rooms`).
  - Worker registration itself mints a short-lived JWT with `VideoGrants(agent=True)` from the connection's `api_key`/`api_secret` — `api.AccessToken(self._api_key, self._api_secret).with_grants(api.VideoGrants(agent=True)).to_jwt()`, sent as `Authorization: Bearer ...` on the worker's WebSocket connect (`worker.py:1088-1091`). So a worker connection is itself a normal signed-JWT credential, scoped by the `agent=True` grant, not a raw key/secret handshake.
  - SIP admin operations need `SIPGrants(admin=True)`; outbound calls need `SIPGrants(call=True)` — don't hand the same broad admin grant to code paths that only dial calls.
  - End-user room-join tokens should stay scoped to `room_join=True` + that one `room`, as you presumably already do.
- **Token lifetimes**: `AccessToken` defaults to `DEFAULT_TTL = 6 hours` (`api/access_token.py`); `TokenVerifier` enforces `require: ["exp"]` on decode specifically because "a hand-rolled token with a valid signature and no exp verifies forever" (comment citing `livekit/protocol#1706`) — always call `.with_ttl(...)` explicitly for anything shorter-lived than 6h (e.g. UI credential-test tokens should probably use a TTL of minutes, not the 6h default, since they're minted ad hoc and not tied to a real session).
- Multi-deployment-specific risk: a leaked/rotated secret for one `LiveKitConnection` only compromises that connection's rooms/dispatch/SIP/egress — encrypting and isolating secrets per-connection (rather than one shared secret store key) contains blast radius, which is the main security argument *for* doing this properly rather than reusing one encryption context for all connections.

---

## 8. Recommended architecture

### Option A — Worker-per-connection supervisor (recommended default)

- **`LiveKitConnection` entity** (Postgres row): `id`, `name`, `deployment_type` (`cloud` | `self_hosted`), `url`, `api_key`, `api_secret_encrypted` (Fernet), `region` (optional, Cloud only), capability flags computed at validation time (below), `created_by`, timestamps.
- **Per-agent connection selection**: your existing agent-config entity gets a `livekit_connection_id` FK; a given agent definition is deployed against exactly one connection at a time (matches §1's one-worker-one-server constraint cleanly — no per-agent multi-connection ambiguity).
- **Worker-per-connection supervisor**: a small process manager (could live inside your FastAPI app as a background task, or be a separate lightweight service) holds a table of `(connection_id, agent_name) → subprocess/container handle`. On "activate agent X on connection Y," it launches (or ensures running) one `AgentServer` process/container with `LIVEKIT_URL/API_KEY/API_SECRET` from connection Y (decrypted just-in-time) and `agent_name=X`. On credential rotation for Y, it restarts every process bound to Y (§1.6). Scale-out for one busy connection = start more identical processes for that same `(connection, agent_name)` pair — they naturally load-balance via the server's own worker-pool dispatch (§1.5), no extra code needed.
- **Trade-off**: N connections × M agents = N×M long-running processes/containers to supervise — more operational surface than today's single process, but it is the only pattern the SDK actually supports (§1.1–1.3), and it isolates failure/restart domains per connection, which is desirable anyway (a bad self-hosted server shouldn't take down your Cloud-project agents).

### Option B — One worker fleet per deployment, agent-name-routed

- Same entity model as A, but instead of one process per `(connection, agent_name)`, run a **smaller number of "fat" worker processes per connection**, each internally routing to multiple agent behaviors based on job metadata/dispatch data rather than relying on the SDK's one-`agent_name`-per-process default dispatch. This requires your entrypoint function to branch on `ctx.job.agent_name`/dispatch metadata itself and load the right agent logic dynamically — technically possible since `JobContext`/`JobRequest` carry the job's requested `agent_name` and you control the entrypoint body, but it works against the grain of `AgentServer`'s one-`rtc_session` design (§1.2): you'd register only **one** `agent_name` per `AgentServer` anyway (since that's still fixed per process), so this "option" really only reduces process count when several agent *behaviors* legitimately share one `agent_name`/entrypoint and branch internally — it does not let one process serve two distinct `agent_name`s. Given that constraint, **Option A is simpler and more literally correct against the SDK**; Option B is only worth it if you have many low-traffic agent variants you want to consolidate under fewer processes and are willing to own the internal routing complexity.

### How the API dispatches to the right fleet

Your FastAPI backend, when minting a token/creating a dispatch for agent X on connection Y, must use connection Y's own `AgentDispatchService`/`AccessToken` (constructed with Y's `url`/`api_key`/`api_secret`) — not a single global `LiveKitAPI` instance. Practically: a small factory `get_livekit_api(connection: LiveKitConnection) -> api.LiveKitAPI` that decrypts and constructs per-request (or a short-lived cache keyed by `connection_id`, invalidated on credential rotation).

### Capability flags per connection

Compute and cache these at "test connection" time (§5) and periodically thereafter:
- `inference_available`: `bool` — true only for LiveKit Cloud connections where you've also configured `LIVEKIT_INFERENCE_*` for that connection's workers (Cloud alone isn't sufficient if you choose not to wire Inference for a given connection — make this an explicit toggle, not an automatic Cloud-implies-true assumption, since it changes whether your UI requires the customer to supply STT/LLM/TTS vendor keys).
- `sip_enabled`: probe via a SIP list call (§5); false for self-hosted connections that haven't deployed the SIP service, or Cloud connections where SIP isn't used.
- `egress_enabled` / `ingress_enabled`: probe via `list_egress`/`list_ingress`; same reasoning.
- `cloud_hosting`: `deployment_type == "cloud"` — gates whether "Deploy to LiveKit Cloud" (§3.4) is offered at all, versus only the universal "run our own worker container" path.
- `noise_cancellation_tier`: `"none" | "ai_coustics_only" | "full_krisp"` based on `deployment_type` and whether an ai-coustics license key is configured for that connection.
- `observability_dashboard`: `deployment_type == "cloud"` (self-host only gets your own exported session reports, §2.3).

### UI surfacing of unavailable features

Disable (don't hide) controls gated by a false capability flag, with an inline explanation sourced from this document's §2.3/§6 table (e.g. "LiveKit Inference requires a LiveKit Cloud project with Inference configured — this connection is self-hosted, so STT/LLM/TTS must use your own vendor API keys"). Surfacing *why*, not just *that*, a feature is unavailable avoids repeat support questions and matches the existing pattern (if any) you use for provider-key-gated features elsewhere in the platform.

---

## Open items marked UNVERIFIED (do not treat as confirmed)

1. Exact list of LiveKit Cloud agent-hosting regions (US East/EU Central/AP South) — sourced from a search-engine summary, not a direct doc fetch.
2. Whether a Python equivalent of the Go `cloudagents.Client` (used internally by `lk agent deploy`, package `github.com/livekit/server-sdk-go/v2/pkg/cloudagents`) exists as a public SDK — not found; `lk agent deploy`'s Cloud build/run API appears to be Go-CLI-only today, meaning a "Deploy to Cloud" button would likely have to shell out to `lk` or reverse-engineer its Twirp/HTTP calls rather than call a documented Python client.
3. Redis's status as strictly mandatory vs strongly recommended for a *single-node* self-hosted deployment — the fetched doc phrased it as "recommended for production," not an unconditional hard requirement for one node.
4. Names/repos of the separate Egress/Ingress/SIP self-host services (`livekit/egress`, `livekit/ingress`, `livekit/sip`) — inferred from doc phrasing ("deployed as a separate service"), not individually opened/confirmed in this session.
5. The exact server-side job-assignment algorithm among multiple available workers of the same `agent_name` pool (§1.5) — lives in `github.com/livekit/livekit`'s server code, which this session did not open; "picks an available, under-threshold worker" is verified via client-visible behavior (`UpdateWorkerStatus`/`load_threshold`), "specifically least-loaded" is not.
6. Whether `AgentDispatchService.list_dispatch` against a nonexistent room name returns an empty list or a `not_found`-style error (§5) — not exercised against a live server in this session, only read from client source.
