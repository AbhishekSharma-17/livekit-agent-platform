"""A fake LiveKit ``SipService``/``RoomService``/``AgentDispatchService`` for V2-17 tests.

The api reaches LiveKit only through ``ConnectionClientFactory.api(row)``;
:class:`FakeClientFactory` overrides that one method to yield a
:class:`FakeLiveKitApi` that records every request (the real protobuf objects
the service built) and answers with realistic ids, so tests assert on exactly
what would have been sent to LiveKit. Nothing here opens a socket.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from livekit.api import (
    CreateAgentDispatchRequest,
    CreateSIPDispatchRuleRequest,
    CreateSIPInboundTrunkRequest,
    CreateSIPOutboundTrunkRequest,
    CreateSIPParticipantRequest,
    DeleteRoomRequest,
    DeleteSIPDispatchRuleRequest,
    DeleteSIPTrunkRequest,
    SendDataRequest,
    SIPDispatchRuleInfo,
    SIPInboundTrunkInfo,
    SIPOutboundTrunkInfo,
    SIPParticipantInfo,
    SIPTransferStatus,
    TransferSIPParticipantRequest,
    TransferSIPParticipantResponse,
)
from livekit.protocol.agent_dispatch import AgentDispatch
from livekit.protocol.room import DeleteRoomResponse, SendDataResponse

from lkap_api.connections.clients import ConnectionClientFactory, ConnectionRowLike
from lkap_api.vault import Vault


@dataclass
class FakeLiveKitApi:
    """Records ``(method, request)`` pairs; ``fail`` maps a method name to an exception."""

    requests: list[tuple[str, Any]] = field(default_factory=list)
    fail: dict[str, Exception] = field(default_factory=dict)
    transfer_status: int = SIPTransferStatus.STS_TRANSFER_SUCCESSFUL
    counter: int = 0

    @property
    def sip(self) -> FakeLiveKitApi:
        return self

    @property
    def room(self) -> FakeLiveKitApi:
        return self

    @property
    def agent_dispatch(self) -> FakeLiveKitApi:
        return self

    def _record(self, method: str, request: Any) -> None:
        self.requests.append((method, request))
        if method in self.fail:
            raise self.fail[method]

    def _next(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.counter}"

    def named(self, method: str) -> list[Any]:
        """Every request sent to ``method``, in order."""
        return [request for name, request in self.requests if name == method]

    # SipService ---------------------------------------------------------------
    async def create_inbound_trunk(self, create: CreateSIPInboundTrunkRequest) -> SIPInboundTrunkInfo:
        self._record("create_inbound_trunk", create)
        info = SIPInboundTrunkInfo()
        info.CopyFrom(create.trunk)
        info.sip_trunk_id = self._next("ST_in")
        return info

    async def create_outbound_trunk(self, create: CreateSIPOutboundTrunkRequest) -> SIPOutboundTrunkInfo:
        self._record("create_outbound_trunk", create)
        info = SIPOutboundTrunkInfo()
        info.CopyFrom(create.trunk)
        info.sip_trunk_id = self._next("ST_out")
        return info

    async def update_inbound_trunk_fields(self, trunk_id: str, **fields: Any) -> SIPInboundTrunkInfo:
        self._record("update_inbound_trunk_fields", {"trunk_id": trunk_id, **fields})
        return SIPInboundTrunkInfo(sip_trunk_id=trunk_id)

    async def update_outbound_trunk_fields(self, trunk_id: str, **fields: Any) -> SIPOutboundTrunkInfo:
        self._record("update_outbound_trunk_fields", {"trunk_id": trunk_id, **fields})
        return SIPOutboundTrunkInfo(sip_trunk_id=trunk_id)

    async def update_outbound_trunk(self, trunk_id: str, trunk: SIPOutboundTrunkInfo) -> SIPOutboundTrunkInfo:
        self._record("update_outbound_trunk", {"trunk_id": trunk_id, "trunk": trunk})
        info = SIPOutboundTrunkInfo()
        info.CopyFrom(trunk)
        info.sip_trunk_id = trunk_id
        return info

    async def delete_trunk(self, delete: DeleteSIPTrunkRequest) -> Any:
        self._record("delete_trunk", delete)
        return None

    async def create_dispatch_rule(self, create: CreateSIPDispatchRuleRequest) -> SIPDispatchRuleInfo:
        self._record("create_dispatch_rule", create)
        info = SIPDispatchRuleInfo()
        info.CopyFrom(create.dispatch_rule)
        info.sip_dispatch_rule_id = self._next("SDR")
        return info

    async def delete_dispatch_rule(self, delete: DeleteSIPDispatchRuleRequest) -> SIPDispatchRuleInfo:
        self._record("delete_dispatch_rule", delete)
        return SIPDispatchRuleInfo(sip_dispatch_rule_id=delete.sip_dispatch_rule_id)

    async def create_sip_participant(
        self, create: CreateSIPParticipantRequest, **_options: Any
    ) -> SIPParticipantInfo:
        self._record("create_sip_participant", create)
        return SIPParticipantInfo(
            participant_id="PA_1",
            participant_identity=create.participant_identity,
            room_name=create.room_name,
            sip_call_id="SCL_outbound_1",
        )

    async def transfer_sip_participant(
        self, transfer: TransferSIPParticipantRequest, **_options: Any
    ) -> TransferSIPParticipantResponse:
        self._record("transfer_sip_participant", transfer)
        return TransferSIPParticipantResponse(
            transfer_id="TR_1",
            status=self.transfer_status,  # type: ignore[arg-type]
            reason=3 if self.transfer_status == SIPTransferStatus.STS_TRANSFER_FAILED else 0,  # type: ignore[arg-type]  # STR_REJECTED
        )

    # RoomService --------------------------------------------------------------
    async def delete_room(self, delete: DeleteRoomRequest) -> DeleteRoomResponse:
        self._record("delete_room", delete)
        return DeleteRoomResponse()

    async def send_data(self, send: SendDataRequest) -> SendDataResponse:
        self._record("send_data", send)
        return SendDataResponse()

    # AgentDispatchService -----------------------------------------------------
    async def create_dispatch(self, req: CreateAgentDispatchRequest) -> AgentDispatch:
        self._record("create_dispatch", req)
        return AgentDispatch(id="AD_1", agent_name=req.agent_name, room=req.room, metadata=req.metadata)


class FakeClientFactory(ConnectionClientFactory):
    """A real factory (credentials still decrypt) whose ``api()`` yields the fake."""

    def __init__(self, vault: Vault, lk: FakeLiveKitApi) -> None:
        super().__init__(vault)
        self.lk = lk
        self.opened: list[tuple[str, float, bool]] = []

    @asynccontextmanager
    async def api(  # type: ignore[override]
        self, row: ConnectionRowLike, *, timeout_s: float = 10.0, failover: bool = True
    ) -> AsyncIterator[Any]:
        self.credentials(row)  # the real decrypt path runs
        self.opened.append((row.id, timeout_s, failover))
        yield self.lk
