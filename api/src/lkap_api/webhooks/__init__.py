"""Outbound webhooks: signed, durable, retried deliveries (CONTRACTS-V2 §1.5, §4.6).

`emit()` is the entry point another package calls when an event happens;
`routers/webhooks.py` is the admin CRUD/test/deliveries surface;
`delivery.py` registers the `webhook_delivery` job handler as an import
side effect so it is always registered once this package is imported.
"""

from __future__ import annotations

from lkap_api.webhooks import delivery as _delivery  # noqa: F401 - registers the job handler
from lkap_api.webhooks.service import emit
from lkap_api.webhooks.signing import sign, verify_signature

__all__ = ["emit", "sign", "verify_signature"]
