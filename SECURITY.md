# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Report them privately
through GitHub's **Security → Report a vulnerability** on this repository.
Include steps to reproduce, the affected package (`api`, `agent`, `mcp`,
`supervisor`, `web`) and the commit you tested.

## Scope notes

- The dev defaults (`LKAP_ADMIN_TOKEN=dev-admin`, `LKAP_SERVICE_TOKEN=dev-service`,
  the break-glass admin token) are for local development only. Production
  (`LKAP_ENV=prod`) refuses them. Set real values from a secrets manager.
- Never commit `.env` files, `LKAP_MASTER_KEY` or LiveKit API secrets. Provider
  secrets live encrypted in the database vault.
- The current security review and its open items are in `docs/v2/REVIEW-V2.md`.
  It is not yet recommended for multi-tenant production (see §8 there).
