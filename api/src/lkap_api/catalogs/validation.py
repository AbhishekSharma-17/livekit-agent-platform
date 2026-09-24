"""Workspace provider-enablement validation (ARCHITECTURE-V2 D-V2-9, PLAN-V2 V2-06).

Registers a validator into `lkap_api.config_service.VALIDATORS` (its
documented extension point) rather than editing that module directly: a
pipeline slot whose provider the workspace has switched off
(`workspace_providers.enabled` false) is a validation error, exactly like an
unavailable or uninstalled provider.

Importing this module has the side effect of registering the validator;
`lkap_api.catalogs.__init__` imports it so any importer of the `catalogs`
package (the providers router) wires it in without touching `main.py`.
"""

from __future__ import annotations

from lkap_contracts.api_models import Issue

from lkap_api.config_service import ValidationContext, register_validator


def disabled_provider_issues(ctx: ValidationContext) -> list[Issue]:
    """One error per pipeline slot whose provider the workspace disabled.

    Public (not `_`-prefixed) so a test can import and register it explicitly
    instead of relying on some other test file having already imported
    :mod:`lkap_api.routers.providers` (and so :mod:`lkap_api.catalogs`) first
    — module-level registration is idempotent (`register_validator` dedups by
    identity) but *when* it first runs otherwise depends on collection order.
    """
    if not ctx.disabled_provider_ids:
        return []
    issues: list[Issue] = []
    for slot, ref in ctx.slots():
        if ref.provider_id in ctx.disabled_provider_ids:
            issues.append(
                Issue(
                    path=f"pipeline.{slot}",
                    message=(
                        f"provider '{ref.provider_id}' is disabled for this workspace "
                        "(providers.disabled) — an admin can re-enable it on the Providers page"
                    ),
                    severity="error",
                )
            )
    return issues


register_validator(disabled_provider_issues)
