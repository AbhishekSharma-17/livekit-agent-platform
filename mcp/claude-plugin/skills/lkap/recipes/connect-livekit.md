# Recipe: connect a LiveKit project

Goal: point the platform at a LiveKit Cloud project (or a self-hosted
server) so agents have somewhere to run.

## 1. Prefer a reference when the user has an env file

If the user says something like "my LiveKit credentials are in
`~/.config/lkap/dev.env`", use `file:` references rather than asking them to
paste the values — they never leave a shell history and the platform
resolves them itself:

`connection_create(...)`
```json
{
  "name": "cloud-main",
  "url": "wss://<project>.livekit.cloud",
  "api_key": "file:~/.config/lkap/dev.env#LIVEKIT_API_KEY",
  "api_secret": "file:~/.config/lkap/dev.env#LIVEKIT_API_SECRET",
  "agent_name": "lkap-agent",
  "deployment_type": "cloud",
  "deployment_mode": "external",
  "is_default": true,
  "test_first": true
}
```

## 2. Or accept an inline paste

If the user pastes the url, key and secret directly into the chat instead,
that is fine too — pass them as plain strings. The response never echoes
the values back (a `plan=true` call would show `<inline secret>` in their
place); only the connection's `fingerprint` comes back.

## 3. Check the result

`connection_create` returns a `ConnectionOut` with `status` (`"ok"` after a
successful `test_first`) and `capabilities` (what the project supports:
`inference_available`, `sip_enabled`, …). If `status` is `"error"`, read
`last_error` before retrying — a common cause is `agent_name` already
belonging to another connection on the same LiveKit project.

## 4. Who runs the worker

`deployment_mode="external"` (the default) means the user runs the worker
process themselves; `connection_get(id, include_worker_env=true)` gives the
exact command template (secrets redacted) to hand back to them. For a
platform-managed pool, create with `deployment_mode="supervised"` instead
and follow with `connection_fleet(id, action="start", replicas=1,
confirm=true)`.

## Related concepts

`lkap_explain("connections-and-pools")`.
