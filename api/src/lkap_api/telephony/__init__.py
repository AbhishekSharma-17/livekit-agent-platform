"""LiveKit-native telephony (PLAN-V2 V2-17, ARCHITECTURE-V2 D-V2-14).

* :mod:`~lkap_api.telephony.models` — re-exports of the route models, which
  live in ``lkap_contracts.api_models`` (R-V2-25).
* :mod:`~lkap_api.telephony.common` — the ``sip_enabled`` gate, LiveKit error
  mapping and workspace-scoped lookups.
* :mod:`~lkap_api.telephony.policy` — the workspace's outbound dialing policy
  and its one chokepoint, ``check_destination`` (R-V2-23).
* :mod:`~lkap_api.telephony.validation` — save-time checks of transfer
  destinations (R-V2-21); importing this package registers them.
* :mod:`~lkap_api.telephony.service` — trunks, dispatch rules and phone
  numbers, mirrored to LiveKit's SIP service.
* :mod:`~lkap_api.telephony.calls` — outbound dialing, hangup, cold transfer,
  DTMF hand-off, the forward-only call status machine and the stuck-call sweep.
* :mod:`~lkap_api.telephony.webhooks` — LiveKit webhook handlers feeding the
  call status machine (imported by ``routers/calls.py`` to register them).
"""

from lkap_api.telephony import validation as _validation  # noqa: F401 - registers the validator
