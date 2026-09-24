# Connections and pools

`connection_list()` returns every connection in the workspace.
A **connection** is one LiveKit deployment — a Cloud project or a
self-hosted server — that agents run on. A workspace can have several;
exactly one is `is_default`. `ConnectionOut.capabilities`
(`ConnectionCapabilities`: `inference_available`, `sip_enabled`,
`egress_enabled`, `ingress_enabled`, `cloud_hosting`,
`noise_cancellation_tier`, `observability_dashboard`, `turn_detector_mode`)
is probed at creation and on demand, so the platform never assumes a
capability a self-hosted server may not have.

## Creating one

`connection_create(name, url, api_key, api_secret, deployment_type=
"cloud"|"self_hosted", agent_name="lkap-agent", deployment_mode="external"|
"supervised"|"cloud_hosted", worker_image="slim"|"full", use_inference=true,
is_default=false, test_first=true)`. `api_key`/`api_secret` are
`SecretInput`s — never pasted back. `test_first` runs the same probe as
`connection_test(id)` before saving, so a bad credential never gets stored as
if it worked. `agent_name` must be unique in the LiveKit project: reusing
another connection's name splits worker dispatch between two pools that
both think they own it.

The api refuses a `url` that resolves to a private, loopback or link-local
address (`details.reason=blocked_destination`) unless an operator has
explicitly allowed it — a LiveKit deployment is reached over the public
internet or a configured private range, never guessed at from inside the
platform's own network.

## Fleet: who runs the worker

`deployment_mode` decides who runs the worker process for a connection:

- `external` — you run the worker yourself (a CLI command, systemd, your own
  container); the platform never starts or stops it. `connection_get(id,
  include_worker_env=true, env_format="env"|"compose"|"lk")` returns a
  redacted template for the command you'll run.
- `supervised` — the platform's own supervisor runs the worker as a
  subprocess or container; `connection_fleet(id, action="start"|"stop"|
  "restart", replicas=, confirm=)` controls it (`stop`/`restart` need
  `confirm=true`) and returns a `FleetStatus` with each `WorkerInstanceOut`'s
  readiness.
- `cloud_hosted` — LiveKit Cloud runs the worker from a deploy bundle
  (`GET /v1/connections/{id}/deploy-bundle`, console-only in v3).

`chat_start` preflights a connection's fleet and refuses with `code=
"no_worker"` rather than minting a session against an empty pool.

## Rotating credentials

`connection_rotate(id, api_key, api_secret, confirm=true)` replaces the
stored credentials and bumps `credentials_version`; a `supervised` pool
rolls its instances afterward. Rotation is destructive-tier because a bad
new credential can take every agent on that connection offline at once.

## Related tools

`connection_list`, `connection_get`, `connection_create`, `connection_test`,
`connection_fleet`, `connection_rotate`.

## Related schemas

`ConnectionOut`, `ConnectionCreate`, `ConnectionUpdate`, `ConnectionInfo`,
`ConnectionCapabilities`, `ConnectionTestResult`, `FleetStatus`,
`WorkerInstanceOut`, `FleetActionIn`.
