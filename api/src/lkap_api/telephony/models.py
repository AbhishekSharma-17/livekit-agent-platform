"""Request/response models of the telephony routes (CONTRACTS-V2 §1.6, §3.4, §4.6).

Promoted into ``lkap_contracts.api_models`` by ruling R-V2-25 (so the console
uses generated TypeScript); this module only re-exports them for the api's own
imports. The patterns live in ``lkap_contracts.telephony`` (R-V2-21).

Secrets are write-only: a trunk's SIP password and a dispatch rule's PIN are
accepted on create/update and never returned (``has_password`` / ``has_pin``).
"""

from __future__ import annotations

from lkap_contracts.api_models import (
    E164,
    CallDtmfIn,
    CallDtmfOut,
    CallReportIn,
    CallTransferIn,
    DispatchRuleCreate,
    DispatchRuleOut,
    InternalTransferIn,
    InternalTransferOut,
    PhoneNumberCreate,
    PhoneNumberOut,
    PhoneNumberUpdate,
    ProviderHint,
    TrunkCreate,
    TrunkDirection,
    TrunkOut,
    TrunkUpdate,
)
from lkap_contracts.telephony import DTMF_PATTERN, E164_PATTERN, TRANSFER_TARGET_PATTERN

__all__ = [
    "DTMF_PATTERN",
    "E164",
    "E164_PATTERN",
    "TRANSFER_TARGET_PATTERN",
    "CallDtmfIn",
    "CallDtmfOut",
    "CallReportIn",
    "CallTransferIn",
    "DispatchRuleCreate",
    "DispatchRuleOut",
    "InternalTransferIn",
    "InternalTransferOut",
    "PhoneNumberCreate",
    "PhoneNumberOut",
    "PhoneNumberUpdate",
    "ProviderHint",
    "TrunkCreate",
    "TrunkDirection",
    "TrunkOut",
    "TrunkUpdate",
]
