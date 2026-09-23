"""Save-time guard for an S3 ``endpoint_url`` an admin sets (V2-22, REVIEW-V2 R2-34).

``storage_configs.endpoint_url`` reaches botocore unguarded. Today no route
writes it (rows come from the operator's ``LKAP_STORAGE_*`` env and the
bootstrap), so nothing calls this yet: **any future route that creates or
updates a storage config must call** :func:`validate_endpoint_url` **before
storing the value**, exactly like webhook and connection urls
(:func:`lkap_api.net_guard.validate_url`, 422 ``blocked_destination``).

The operator's own ``LKAP_STORAGE_ENDPOINT_URL`` is not checked: the prod
compose points it at ``http://minio:9000``, a private address on purpose. An
admin who needs a private endpoint gets it through
``LKAP_NET_ALLOW_PRIVATE_HOSTS``, the same allowlist every other surface uses.
"""

from __future__ import annotations

from lkap_api import net_guard
from lkap_api.settings import Settings

__all__ = ["validate_endpoint_url"]


def validate_endpoint_url(url: str | None, settings: Settings) -> None:
    """Refuse an admin-set S3 endpoint on a private, local or metadata address.

    Args:
        url: The ``endpoint_url`` being saved (``None`` or blank = AWS's own endpoint).
        settings: The process settings (the ``LKAP_NET_ALLOW_PRIVATE_HOSTS`` policy).

    Raises:
        UnprocessableEntityError: ``details.reason == "blocked_destination"``.
    """
    if url is None or not url.strip():
        return
    policy = net_guard.policy_from_settings(settings)
    net_guard.validate_url(url, policy, field_name="endpoint_url")
