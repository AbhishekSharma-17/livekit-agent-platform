"""MCP OAuth: the api as the OAuth client of remote MCP servers (V5-14, V5-16).

research-v4 tools §4.3: the console (api) runs discovery, registration, consent and
the code exchange, and keeps the tokens in an ``mcp-oauth`` credential in the vault.
The worker never sees a refresh token, a client secret or a token endpoint: it gets a
short-lived access token in the resolved config and fresh ones from
``POST /internal/v1/tools/{id}/oauth/token`` (V5-16), which refreshes under a lock.

Modules: :mod:`.http` (URL checks, bounded no-redirect fetches), :mod:`.discovery`,
:mod:`.registration`, :mod:`.flows`, :mod:`.callback`, :mod:`.credential`,
:mod:`.service` (start and status), :mod:`.tokens` (refresh, the worker's token),
:mod:`.revoke` (disconnect), :mod:`.router`, :mod:`.logsafe`.
"""
