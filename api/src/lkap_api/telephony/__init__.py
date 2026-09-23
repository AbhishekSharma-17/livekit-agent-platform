"""LiveKit-native telephony (PLAN-V2 V2-17, ARCHITECTURE-V2 D-V2-14).

* :mod:`~lkap_api.telephony.models` — request/response models of the routes.
* :mod:`~lkap_api.telephony.common` — the ``sip_enabled`` gate, LiveKit error
  mapping and workspace-scoped lookups.
* :mod:`~lkap_api.telephony.service` — trunks, dispatch rules and phone
  numbers, mirrored to LiveKit's SIP service.
* :mod:`~lkap_api.telephony.calls` — outbound dialing, hangup, cold transfer,
  DTMF hand-off and the forward-only call status machine.
* :mod:`~lkap_api.telephony.webhooks` — LiveKit webhook handlers feeding the
  call status machine (imported by ``routers/calls.py`` to register them).
"""
