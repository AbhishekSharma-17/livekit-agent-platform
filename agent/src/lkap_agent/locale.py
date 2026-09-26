"""The caller's current date and time (R-V5-10).

Two timezones are kept apart: the **business** timezone (`AgentConfig.timezone`,
which the api resolves to the effective zone: the agent's own, else the
workspace default, else UTC) and the **caller's** timezone, resolved once per
session by :func:`ensure_session_locale`:

1. `locale.caller_timezone == "business"` pins the business zone;
2. the caller's `lkap.tz` participant attribute (the browser's zone, validated
   by the api at connect) — source `browser`;
3. on a phone call, the caller's E.164 number when `phonenumbers` maps it to
   **exactly one** zone — source `number`;
4. the business zone — source `business` (`default` when even that is not a
   valid IANA name and UTC is used).

The agent learns it from one stamp line in the system prompt, composed once
(:meth:`SessionLocale.stamp`, used by `platform_agent.compose_instructions` for
prompt and flow agents alike), and from a short "Time now" note appended at the
tail of the chat context once 15 minutes have passed or the caller's day
changed (:meth:`SessionLocale.due_refresh`) — never by rewriting the system
prompt, which would defeat provider prompt caching every turn.

Every clock read goes through :attr:`SessionLocale.clock`, so tests inject a
fixed time and never depend on the machine's own timezone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import phonenumbers
from livekit import rtc
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.api_models import LocaleSource
from lkap_contracts.common import CALLER_TIMEZONE_ATTRIBUTE, is_iana_timezone
from phonenumbers import timezone as phone_timezones

from lkap_agent.logging import get_logger
from lkap_agent.observability import LOCALE_EVENT, locale_event_payload

__all__ = [
    "CALLER_TIME_RULE",
    "LOCALE_USERDATA_KEY",
    "REFRESH_AFTER",
    "CallerZone",
    "Clock",
    "SessionLocale",
    "business_zone_of",
    "caller_participant",
    "describe_moment",
    "ensure_session_locale",
    "fallback_locale",
    "local_date",
    "moment_payload",
    "parse_clock_time",
    "parse_moment",
    "resolve_caller_zone",
    "session_locale",
    "utc_now",
    "utc_offset",
    "zone_for_number",
]

logger = get_logger(__name__)

#: A zero-argument function returning an aware `datetime` (the session's clock).
Clock = Callable[[], datetime]

#: `SessionContext.userdata` key holding the session's :class:`SessionLocale`.
LOCALE_USERDATA_KEY: Final[str] = "_lkap_locale"

#: A "Time now" note is appended once this long has passed since the last stamp.
REFRESH_AFTER: Final[timedelta] = timedelta(minutes=15)

#: Added to the stamp only when the caller's and the business's zones differ.
CALLER_TIME_RULE: Final[str] = (
    "Use the caller's time for 'today', 'tomorrow' and any time you say; "
    "convert opening hours from the business timezone."
)

#: Participant attributes of a SIP leg (livekit-agents 1.8.3, `lkap_agent.telephony`).
_ATTR_PHONE_NUMBER: Final[str] = "sip.phoneNumber"
#: Set on an avatar's participant, which publishes on behalf of the agent.
_ATTR_PUBLISH_ON_BEHALF: Final[str] = "lk.publish_on_behalf"
_SIP_CHANNELS: Final[frozenset[str]] = frozenset({"sip_in", "sip_out"})

# English names, independent of the process locale (`strftime("%A")` is not).
_WEEKDAYS: Final[tuple[str, ...]] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTHS: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def utc_now() -> datetime:
    """The default session clock: now, in UTC."""
    return datetime.now(UTC)


def utc_offset(moment: datetime) -> str:
    """``+05:30`` style offset of an aware `datetime` (``+00:00`` for UTC)."""
    offset = moment.utcoffset() or timedelta(0)
    sign = "-" if offset < timedelta(0) else "+"
    minutes = abs(int(offset.total_seconds())) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _day(local: datetime) -> str:
    return f"{_WEEKDAYS[local.weekday()]} {local.day} {_MONTHS[local.month - 1]} {local.year}"


def describe_moment(moment: datetime, zone: str) -> str:
    """``Friday 26 September 2026, 01:45`` for `moment` seen in `zone`."""
    local = moment.astimezone(ZoneInfo(zone))
    return f"{_day(local)}, {local:%H:%M}"


def local_date(moment: datetime, zone: str) -> date:
    """The calendar date of `moment` in `zone`."""
    return moment.astimezone(ZoneInfo(zone)).date()


def moment_payload(moment: datetime, zone: str) -> dict[str, str]:
    """The `current_time` / `convert_time` block for `moment` seen in `zone`."""
    local = moment.astimezone(ZoneInfo(zone))
    return {
        "now_iso": local.isoformat(timespec="seconds"),
        "weekday": _WEEKDAYS[local.weekday()],
        "date": local.date().isoformat(),
        "time": f"{local:%H:%M}",
        "timezone": zone,
        "utc_offset": utc_offset(local),
    }


def zone_for_number(number: str | None) -> str | None:
    """The caller's zone from an E.164 number, only when the number maps to exactly one.

    `phonenumbers.timezone.time_zones_for_number` answers ``("Etc/Unknown",)`` for
    a number it cannot place; several zones (most mobile ranges, toll-free
    numbers, multi-zone countries) are ambiguous. Both give ``None``, as does a
    number that does not parse.
    """
    if not number:
        return None
    try:
        parsed = phonenumbers.parse(number, None)
    except phonenumbers.NumberParseException:
        return None
    zones = [zone for zone in phone_timezones.time_zones_for_number(parsed) if is_iana_timezone(zone)]
    return zones[0] if len(zones) == 1 else None


def business_zone_of(config: AgentConfig) -> tuple[str, LocaleSource]:
    """The business timezone and its source: ``config.timezone``, else UTC (``default``)."""
    if is_iana_timezone(config.timezone):
        return config.timezone, "business"
    return "UTC", "default"


@dataclass(frozen=True, slots=True)
class CallerZone:
    """The caller's zone and where it came from."""

    timezone: str
    source: LocaleSource


def resolve_caller_zone(
    config: AgentConfig,
    *,
    attributes: Mapping[str, str] | None = None,
    phone_number: str | None = None,
) -> CallerZone:
    """Apply the R-V5-10 priority chain (see the module docstring).

    Args:
        config: The session's agent config (its `timezone` is the business zone).
        attributes: The caller participant's attributes (`lkap.tz`).
        phone_number: The caller's E.164 number, on a phone call only.

    Returns:
        The caller's zone, always a valid IANA name.
    """
    business, business_source = business_zone_of(config)
    if config.locale.caller_timezone == "business":
        return CallerZone(business, business_source)
    browser = (attributes or {}).get(CALLER_TIMEZONE_ATTRIBUTE)
    if browser and is_iana_timezone(browser):
        return CallerZone(browser, "browser")
    from_number = zone_for_number(phone_number)
    if from_number is not None:
        return CallerZone(from_number, "number")
    return CallerZone(business, business_source)


@dataclass(slots=True)
class SessionLocale:
    """The session's two zones and the stamp/refresh book-keeping."""

    caller_timezone: str
    source: LocaleSource
    business_timezone: str
    started_at: datetime
    clock: Clock = utc_now
    last_stamp_at: datetime | None = field(default=None)

    def __post_init__(self) -> None:
        if self.last_stamp_at is None:
            self.last_stamp_at = self.started_at

    @property
    def zones_differ(self) -> bool:
        """Whether the caller's and the business's zones are different names."""
        return self.caller_timezone != self.business_timezone

    def stamp(self) -> str:
        """The one system-prompt line, fixed at session start (identical for every recomposition)."""
        local = self.started_at.astimezone(ZoneInfo(self.caller_timezone))
        line = (
            f"Current date and time: {describe_moment(self.started_at, self.caller_timezone)} "
            f"({self.caller_timezone}, UTC{utc_offset(local)})."
        )
        if not self.zones_differ:
            return line
        there = self.started_at.astimezone(ZoneInfo(self.business_timezone))
        when = (
            f"{there:%H:%M}"
            if there.date() == local.date()
            else f"{_WEEKDAYS[there.weekday()]} {there:%H:%M}"
        )
        return f"{line} The business runs on {self.business_timezone} ({when} there). {CALLER_TIME_RULE}"

    def _caller_date(self, moment: datetime) -> date:
        return moment.astimezone(ZoneInfo(self.caller_timezone)).date()

    def due_refresh(self, now: datetime) -> bool:
        """Whether a "Time now" note is due: 15 minutes since the last stamp, or a new day for the caller."""
        last = self.last_stamp_at or self.started_at
        return now - last >= REFRESH_AFTER or self._caller_date(now) != self._caller_date(last)

    def refresh_note(self, now: datetime) -> str:
        """``Time now: 02:01 (Asia/Kolkata)``; the full date when the caller's day changed."""
        last = self.last_stamp_at or self.started_at
        local = now.astimezone(ZoneInfo(self.caller_timezone))
        if self._caller_date(now) != self._caller_date(last):
            return f"Time now: {describe_moment(now, self.caller_timezone)} ({self.caller_timezone})"
        return f"Time now: {local:%H:%M} ({self.caller_timezone})"

    def mark_stamped(self, now: datetime) -> None:
        """Record that the model has just been told the time."""
        self.last_stamp_at = now

    def resolve_zone(self, name: str | None) -> str | None:
        """A tool's zone argument: ``None``/``caller`` → the caller's, ``business`` → the business's.

        Returns ``None`` for a name that is not an IANA timezone.
        """
        if name is None or not name.strip() or name.strip().lower() == "caller":
            return self.caller_timezone
        if name.strip().lower() == "business":
            return self.business_timezone
        return name.strip() if is_iana_timezone(name.strip()) else None


def session_locale(ctx: Any) -> SessionLocale | None:
    """The session's :class:`SessionLocale`, once :func:`ensure_session_locale` ran."""
    userdata = getattr(ctx, "userdata", None)
    value = userdata.get(LOCALE_USERDATA_KEY) if isinstance(userdata, dict) else None
    return value if isinstance(value, SessionLocale) else None


def fallback_locale(config: AgentConfig, *, clock: Clock = utc_now) -> SessionLocale:
    """A locale from the config alone (the caller is assumed to be in the business zone)."""
    business, source = business_zone_of(config)
    return SessionLocale(
        caller_timezone=business, source=source, business_timezone=business, started_at=clock(), clock=clock
    )


def caller_participant(room: Any, *, linked: Any = None) -> Any:
    """The caller's participant: the one RoomIO linked, else the best match in the room.

    Preference: the linked participant; one carrying `lkap.tz` (only the api's
    caller token has it); the SIP leg; the first remote participant that is
    neither an agent nor an avatar. ``None`` when the room has none yet.
    """
    if linked is not None:
        return linked
    participants = list((getattr(room, "remote_participants", None) or {}).values())
    for participant in participants:
        if CALLER_TIMEZONE_ATTRIBUTE in (getattr(participant, "attributes", None) or {}):
            return participant
    for participant in participants:
        if getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return participant
    for participant in participants:
        attributes = getattr(participant, "attributes", None) or {}
        if attributes.get(_ATTR_PUBLISH_ON_BEHALF):
            continue
        if getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            continue
        return participant
    return None


def ensure_session_locale(ctx: Any, *, linked: Any = None, clock: Clock | None = None) -> SessionLocale:
    """Resolve the session's locale once, record the `locale` event, and return it.

    Idempotent: later calls (every flow node's entry, the tools) return the
    stored value. The event is recorded through `ctx.record_event` once per
    session. Never raises: a failure to read the room falls back to the
    business zone.

    Args:
        ctx: The session's `PackSessionContext` (`config`, `room`, `channel`, `userdata`).
        linked: The participant RoomIO linked, when known.
        clock: The clock to use (tests); defaults to UTC wall time.

    Returns:
        The session's :class:`SessionLocale`.
    """
    existing = session_locale(ctx)
    if existing is not None:
        return existing
    config = cast(AgentConfig, ctx.config)
    tick = clock or utc_now
    attributes: Mapping[str, str] = {}
    phone_number: str | None = None
    try:
        participant = caller_participant(getattr(ctx, "room", None), linked=linked)
        if participant is not None:
            attributes = dict(getattr(participant, "attributes", None) or {})
            if getattr(ctx, "channel", "web") in _SIP_CHANNELS:
                phone_number = attributes.get(_ATTR_PHONE_NUMBER) or None
    except Exception:
        logger.debug("could not read the caller's participant for the locale", exc_info=True)
    zone = resolve_caller_zone(config, attributes=attributes, phone_number=phone_number)
    business, _ = business_zone_of(config)
    locale = SessionLocale(
        caller_timezone=zone.timezone,
        source=zone.source,
        business_timezone=business,
        started_at=tick(),
        clock=tick,
    )
    userdata = getattr(ctx, "userdata", None)
    if isinstance(userdata, dict):
        userdata[LOCALE_USERDATA_KEY] = locale
    logger.info(
        "caller timezone resolved",
        caller_timezone=locale.caller_timezone,
        source=locale.source,
        business_timezone=locale.business_timezone,
    )
    record = getattr(ctx, "record_event", None)
    if callable(record):
        try:
            record(
                LOCALE_EVENT,
                locale_event_payload(
                    caller_timezone=locale.caller_timezone,
                    source=locale.source,
                    business_timezone=locale.business_timezone,
                ),
            )
        except Exception:
            logger.debug("could not record the locale event", exc_info=True)
    return locale


def parse_clock_time(text: str) -> time | None:
    """``14:30``, ``2:30 pm``, ``9am``, ``14:30:15`` → a `time`; ``None`` when it does not parse."""
    raw = text.strip().lower().replace(".", "")
    suffix = None
    for marker in ("am", "pm"):
        if raw.endswith(marker):
            suffix = marker
            raw = raw[: -len(marker)].strip()
    parts = raw.split(":")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        return None
    hour, minute, second = (int(p) for p in (*parts, "0", "0")[:3])
    if suffix is not None:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return time(hour, minute, second)


def parse_moment(text: str, zone: str, *, today: date) -> datetime | None:
    """A wall-clock time (`today` in `zone`) or an ISO date-time, as an aware `datetime` in `zone`."""
    tz = ZoneInfo(zone)
    clock_time = parse_clock_time(text)
    if clock_time is not None:
        return datetime.combine(today, clock_time, tzinfo=tz)
    try:
        parsed = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed
