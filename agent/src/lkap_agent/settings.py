"""Runtime settings for the `lkap-agent` worker.

Shapes and env var names follow docs/CONTRACTS.md §3 and CONTRACTS-V2 §5/§6.
`.env` is optional and human-created; ship `env.example` only. Never read
`.env*` from tooling.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The dispatch name a worker registers under when `LKAP_AGENT_NAME` is unset
#: (CONTRACTS-V2 §6; `livekit_connections.agent_name` defaults to the same value).
DEFAULT_AGENT_NAME = "lkap-agent"

#: The `User-Agent` HTTP tools send when a tool sets none (asks #29 / B-4). Some
#: public APIs refuse anonymous clients: Wikimedia answers 403 to httpx unless
#: the User-Agent carries contact info, and a project URL counts as one.
DEFAULT_HTTP_TOOL_USER_AGENT = "LKAP/0.1 (+https://github.com/AbhishekSharma-17/livekit-agent-platform)"


#: `LKAP_MCP_ALLOWED_HOSTS` value that reuses the HTTP-tool list (D-V5-4's override).
MCP_HOSTS_HTTP_ALIAS = "@http"


class Settings(BaseSettings):
    """Agent worker settings.

    Fields without the `LIVEKIT_` canonical alias are read from
    `LKAP_<FIELD_NAME>` (env_prefix="LKAP_"). LiveKit's own env var names are
    canonical and never carry the `LKAP_` prefix, so they use an explicit
    `validation_alias`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LKAP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LiveKit canonical names (no LKAP_ prefix) ---
    livekit_url: str = Field(validation_alias="LIVEKIT_URL")
    livekit_api_key: str = Field(validation_alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(validation_alias="LIVEKIT_API_SECRET")
    #: `LIVEKIT_AGENT_NAME`: optional and informational only. The worker
    #: registers under `agent_name`; a *set* value that differs from it is
    #: refused at start-up (D-W2-11), because it means the process was launched
    #: from another agent's environment.
    livekit_agent_name: str | None = Field(default=None, validation_alias="LIVEKIT_AGENT_NAME")

    # --- LKAP_-prefixed ---
    #: `LKAP_AGENT_NAME`: the dispatch name this worker registers under and the
    #: only one its job filter accepts (CONTRACTS-V2 §5). One per connection;
    #: the supervisor (or the operator, in `external` mode) sets it.
    agent_name: str = DEFAULT_AGENT_NAME
    #: `LKAP_CONNECTION_ID`: the connection this worker serves; unset ⇒ the api
    #: attributes the worker to the default connection when it registers.
    connection_id: str | None = None
    #: `LKAP_INSTANCE_KEY`: override for the `worker_instances.instance_key`
    #: this process registers under; unset ⇒ `hostname:pid`.
    instance_key: str | None = None
    #: `LKAP_MANAGED_BY`: who started this process, reported at registration;
    #: the supervisor sets `supervisor` (docs/v2/_asks.md #46).
    managed_by: Literal["external", "supervisor", "cloud"] = "external"
    #: `LKAP_IMAGE_FLAVOR`: which worker image this process runs (the build
    #: arg of `agent/Dockerfile`); a dev venv counts as `slim`.
    image_flavor: Literal["slim", "full"] = "slim"
    #: `LKAP_INSTALLED_PROVIDERS_FILE`: the build-time import check's output
    #: (CONTRACTS-V2 §7). When absent, installed providers are derived from the
    #: distributions present in this environment.
    installed_providers_file: str = "/app/installed_providers.json"
    #: `LKAP_HEARTBEAT_INTERVAL_S`: seconds between `workers/{key}/heartbeat` posts.
    heartbeat_interval_s: float = 30.0
    #: `LKAP_RECONNECT_GRACE_S`: how long a job survives its caller dropping off
    #: the room before it shuts down (REVIEW-FINAL F-33). `None`/`0` ends the
    #: job as soon as the caller leaves.
    reconnect_grace_s: float | None = 60.0
    #: `LKAP_SIP_ANSWER_TIMEOUT_S`: how long an outbound (`sip_out`) job waits for
    #: the callee to answer before it gives up without speaking (R-V2-20). A
    #: backstop only: the api deletes the room when the dial fails, which ends
    #: the wait at once. Default = the api's `MAX_RING_S + DIAL_MARGIN_S`. SIP
    #: jobs get no reconnect grace: a phone leg never rejoins.
    sip_answer_timeout_s: float = 135.0

    service_token: str
    api_base_url: str
    packs: str = "packs.insurance_claim,packs.generic"
    http_tool_allowed_hosts: str = ""
    #: `LKAP_MCP_ALLOWED_HOSTS` (V5-09, D-V5-4): comma-separated MCP server hosts. Empty = any
    #: public `https` host; non-empty = a ceiling no MCP server may leave; `@http` = reuse
    #: `LKAP_HTTP_TOOL_ALLOWED_HOSTS` (empty then allows nothing). The api reads the same
    #: variable at save time and for the connection test.
    mcp_allowed_hosts: str = ""
    #: `LKAP_HTTP_TOOL_USER_AGENT`: the `User-Agent` declarative HTTP tools and the
    #: built-in `http_request` send when the tool's own headers set none. Operators
    #: should put their own contact URL or email here.
    http_tool_user_agent: str = DEFAULT_HTTP_TOOL_USER_AGENT
    log_level: str = "INFO"
    log_json: bool = False
    vision_max_frame_age_s: float = 8.0
    #: `LKAP_IDLE_HANGUP_S`: seconds a session may stay idle before the worker
    #: hangs up (REVIEW-FINAL F-02). The clock starts when the SDK reports the
    #: user `away` while the agent is listening or idle, and any other user state
    #: cancels it. `None` or `0` disables the hangup. The `away` state is only
    #: ever emitted when the agent's `voice.user_away_timeout_s` is set, so an
    #: agent with `user_away_timeout_s=None` never idles out; the effective idle
    #: time is `user_away_timeout_s + idle_hangup_s`.
    idle_hangup_s: float | None = 120.0

    @property
    def packs_list(self) -> list[str]:
        """`LKAP_PACKS` split into an ordered list of dotted module paths."""
        return [p.strip() for p in self.packs.split(",") if p.strip()]

    @property
    def http_tool_allowed_hosts_list(self) -> list[str]:
        """`LKAP_HTTP_TOOL_ALLOWED_HOSTS` split into a host allowlist (may be empty)."""
        return [h.strip() for h in self.http_tool_allowed_hosts.split(",") if h.strip()]

    @property
    def mcp_host_ceiling(self) -> frozenset[str] | None:
        """`LKAP_MCP_ALLOWED_HOSTS` as a ceiling: `None` = no ceiling, a set = the only hosts.

        `@http` returns the HTTP-tool list, so an empty one allows no MCP server at all.
        """
        raw = self.mcp_allowed_hosts.strip()
        source = self.http_tool_allowed_hosts if raw == MCP_HOSTS_HTTP_ALIAS else raw
        hosts = frozenset(h.strip().lower().rstrip(".") for h in source.split(",") if h.strip())
        if raw != MCP_HOSTS_HTTP_ALIAS and not hosts:
            return None
        return hosts


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached `Settings` instance."""
    return Settings()
