---
name: add_http_tool
description: Add a new HTTP-backed tool to an existing agent.
arguments: agent
concepts: tools-http
recipes: add-http-tool
---
Add an HTTP tool to agent "{agent}". Ask what the tool should do and which
one URL it calls; require a non-empty `allowed_hosts` (the api refuses to
save one without it). If the request needs an auth header or API key, store
it as a separate `http-tool-secret` credential first — never put a raw
secret in the tool's `url`, `headers` or `body_template` directly. Run the
dry run before attaching it to the agent.
