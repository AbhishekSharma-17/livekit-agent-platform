"""MCP OAuth, part 1: the api as the OAuth client of remote MCP servers (V5-14).

research-v4 tools §4.3: the console (api) runs discovery, registration, consent and
the code exchange, and keeps the tokens in an ``mcp-oauth`` credential in the vault.
The worker never sees a refresh token, a client secret or a token endpoint; handing
it a short-lived access token is V5-16, as are refresh and revocation.

Modules: :mod:`.http` (URL checks, bounded no-redirect fetches), :mod:`.discovery`,
:mod:`.registration`, :mod:`.flows`, :mod:`.callback`, :mod:`.credential`,
:mod:`.service` (start and status), :mod:`.router`, :mod:`.logsafe`.
"""
