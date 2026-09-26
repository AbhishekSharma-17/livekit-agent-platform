"""R-V5-10: the caller's timezone chain, the prompt stamp and the refresh rule (`lkap_agent.locale`).

Every clock is injected: nothing here reads the machine's time or timezone.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from lkap_contracts.agent_config import AgentConfig, LocaleConfig

from lkap_agent.locale import (
    CALLER_TIME_RULE,
    LOCALE_USERDATA_KEY,
    SessionLocale,
    caller_participant,
    ensure_session_locale,
    parse_clock_time,
    resolve_caller_zone,
    session_locale,
    utc_offset,
    zone_for_number,
)

#: 20:15 UTC on Friday 25 September 2026: 01:45 on Saturday in Kolkata, 21:15 on Friday in London.
NOW = datetime(2026, 9, 25, 20, 15, tzinfo=UTC)

#: Fixture numbers (fictional subscriber digits in real ranges).
ONE_ZONE_NUMBER = "+12125550123"  # New York City: one zone
MULTI_ZONE_NUMBER = "+61412345678"  # an Australian mobile: several zones
UNPARSEABLE_NUMBER = "not a number"


def _config(timezone: str = "Europe/London", mode: str = "detect") -> AgentConfig:
    return default_agent_config(timezone=timezone, locale=LocaleConfig(caller_timezone=mode))  # type: ignore[arg-type]


def _locale(caller: str = "Asia/Kolkata", business: str = "Europe/London", **kwargs: Any) -> SessionLocale:
    return SessionLocale(
        caller_timezone=caller, source="browser", business_timezone=business, started_at=NOW, **kwargs
    )


# ------------------------------------------------------------------ phone numbers


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (ONE_ZONE_NUMBER, "America/New_York"),
        (MULTI_ZONE_NUMBER, None),
        (UNPARSEABLE_NUMBER, None),
        ("+15555550123", None),  # an unassigned range: phonenumbers answers Etc/Unknown
        ("", None),
        (None, None),
    ],
    ids=["one-zone", "multi-zone", "unparseable", "unknown-range", "empty", "none"],
)
def test_zone_for_number_only_trusts_a_single_zone(number: str | None, expected: str | None) -> None:
    assert zone_for_number(number) == expected


# ------------------------------------------------------------------ the priority chain


def test_the_browser_attribute_wins() -> None:
    zone = resolve_caller_zone(
        _config(), attributes={"lkap.tz": "Asia/Kolkata"}, phone_number=ONE_ZONE_NUMBER
    )

    assert (zone.timezone, zone.source) == ("Asia/Kolkata", "browser")


def test_a_number_with_one_zone_is_next() -> None:
    zone = resolve_caller_zone(_config(), attributes={}, phone_number=ONE_ZONE_NUMBER)

    assert (zone.timezone, zone.source) == ("America/New_York", "number")


@pytest.mark.parametrize("number", [MULTI_ZONE_NUMBER, UNPARSEABLE_NUMBER, None])
def test_an_ambiguous_or_missing_number_falls_back_to_the_business_zone(number: str | None) -> None:
    zone = resolve_caller_zone(_config(), attributes={}, phone_number=number)

    assert (zone.timezone, zone.source) == ("Europe/London", "business")


def test_an_invalid_attribute_is_ignored() -> None:
    zone = resolve_caller_zone(_config(), attributes={"lkap.tz": "Mars/Base"})

    assert zone.source == "business"


def test_an_invalid_business_zone_falls_back_to_utc() -> None:
    zone = resolve_caller_zone(_config(timezone="Not/AZone"))

    assert (zone.timezone, zone.source) == ("UTC", "default")


def test_business_mode_pins_the_business_zone() -> None:
    zone = resolve_caller_zone(
        _config(mode="business"), attributes={"lkap.tz": "Asia/Kolkata"}, phone_number=ONE_ZONE_NUMBER
    )

    assert (zone.timezone, zone.source) == ("Europe/London", "business")


def test_an_agent_without_locale_behaves_as_detect() -> None:
    """Compatibility: a config saved before `locale` detects the caller's zone."""
    config = AgentConfig.model_validate({"instructions": "Hi", "pipeline": {}, "timezone": "Europe/London"})

    zone = resolve_caller_zone(config, attributes={"lkap.tz": "Asia/Kolkata"})

    assert zone.timezone == "Asia/Kolkata"


# ------------------------------------------------------------------ the stamp


def test_the_stamp_names_both_zones_and_the_rule_when_they_differ() -> None:
    assert _locale().stamp() == (
        "Current date and time: Saturday 26 September 2026, 01:45 (Asia/Kolkata, UTC+05:30). "
        f"The business runs on Europe/London (Friday 21:15 there). {CALLER_TIME_RULE}"
    )


def test_the_stamp_shows_only_the_time_there_on_the_same_day() -> None:
    stamp = _locale(caller="Asia/Kolkata", business="Asia/Dubai").stamp()

    assert stamp == (
        "Current date and time: Saturday 26 September 2026, 01:45 (Asia/Kolkata, UTC+05:30). "
        f"The business runs on Asia/Dubai (00:15 there). {CALLER_TIME_RULE}"
    )


def test_the_stamp_is_one_sentence_when_the_zones_match() -> None:
    stamp = _locale(caller="Europe/London", business="Europe/London").stamp()

    assert stamp == "Current date and time: Friday 25 September 2026, 21:15 (Europe/London, UTC+01:00)."


@pytest.mark.parametrize(
    ("zone", "expected"),
    [("UTC", "+00:00"), ("Asia/Kolkata", "+05:30"), ("America/St_Johns", "-02:30")],
)
def test_utc_offset_formats_half_hours_and_negatives(zone: str, expected: str) -> None:
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    assert utc_offset(NOW.astimezone(ZoneInfo(zone))) == expected


# ------------------------------------------------------------------ the refresh rule


def test_no_refresh_within_fifteen_minutes() -> None:
    locale = _locale()

    assert locale.due_refresh(NOW + timedelta(minutes=14, seconds=59)) is False


def test_a_refresh_after_fifteen_minutes() -> None:
    locale = _locale()
    later = NOW + timedelta(minutes=16)

    assert locale.due_refresh(later) is True
    assert locale.refresh_note(later) == "Time now: 02:01 (Asia/Kolkata)"


def test_the_refresh_clock_restarts_after_a_note() -> None:
    locale = _locale()
    locale.mark_stamped(NOW + timedelta(minutes=16))

    assert locale.due_refresh(NOW + timedelta(minutes=30)) is False
    assert locale.due_refresh(NOW + timedelta(minutes=31)) is True


def test_a_day_change_for_the_caller_refreshes_early_with_the_date() -> None:
    # 18:25 UTC = 23:55 in Kolkata; five minutes later it is Saturday there.
    start = datetime(2026, 9, 25, 18, 25, tzinfo=UTC)
    locale = SessionLocale(
        caller_timezone="Asia/Kolkata", source="browser", business_timezone="Europe/London", started_at=start
    )
    later = start + timedelta(minutes=5)

    assert locale.due_refresh(later) is True
    assert locale.refresh_note(later) == "Time now: Saturday 26 September 2026, 00:00 (Asia/Kolkata)"


# ------------------------------------------------------------------ the session


def _ctx(room: FakeRoom, *, channel: str = "web", **config: Any) -> FakePackSessionContext:
    ctx = FakePackSessionContext(room=room, config=_config(**config))  # type: ignore[arg-type]
    ctx.channel = channel  # type: ignore[attr-defined]
    return ctx


def test_ensure_session_locale_reads_the_browser_attribute_and_records_one_event() -> None:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant("user-1", attributes={"lkap.tz": "Asia/Kolkata"}))
    ctx = _ctx(room)

    first = ensure_session_locale(ctx, clock=lambda: NOW)
    second = ensure_session_locale(ctx, clock=lambda: NOW + timedelta(hours=1))

    assert first is second is session_locale(ctx)
    assert ctx.userdata[LOCALE_USERDATA_KEY] is first
    assert (first.caller_timezone, first.source, first.started_at) == ("Asia/Kolkata", "browser", NOW)
    assert ctx.events == [
        (
            "locale",
            {"caller_timezone": "Asia/Kolkata", "source": "browser", "business_timezone": "Europe/London"},
        )
    ]


def test_ensure_session_locale_infers_a_phone_callers_zone() -> None:
    room = FakeRoom()
    room.add_remote_participant(
        FakeRemoteParticipant(
            "sip-1",
            attributes={"sip.phoneNumber": ONE_ZONE_NUMBER},
            kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        )
    )

    locale = ensure_session_locale(_ctx(room, channel="sip_in"), clock=lambda: NOW)

    assert (locale.caller_timezone, locale.source) == ("America/New_York", "number")


def test_a_number_is_only_read_on_a_phone_call() -> None:
    room = FakeRoom()
    room.add_remote_participant(
        FakeRemoteParticipant("user-1", attributes={"sip.phoneNumber": ONE_ZONE_NUMBER})
    )

    locale = ensure_session_locale(_ctx(room), clock=lambda: NOW)

    assert locale.source == "business"


def test_without_a_caller_the_business_zone_is_used_as_today() -> None:
    locale = ensure_session_locale(_ctx(FakeRoom(), timezone="America/Chicago"), clock=lambda: NOW)

    assert (locale.caller_timezone, locale.source, locale.business_timezone) == (
        "America/Chicago",
        "business",
        "America/Chicago",
    )


def test_the_linked_participant_is_preferred() -> None:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant("other", attributes={"lkap.tz": "Europe/Paris"}))
    linked = FakeRemoteParticipant("user-1", attributes={"lkap.tz": "Asia/Kolkata"})

    locale = ensure_session_locale(_ctx(room), linked=linked, clock=lambda: NOW)

    assert locale.caller_timezone == "Asia/Kolkata"


def test_caller_participant_skips_an_avatar_and_prefers_the_stamped_caller() -> None:
    room = FakeRoom()
    avatar = room.add_remote_participant(
        FakeRemoteParticipant("avatar", attributes={"lk.publish_on_behalf": "agent"})
    )
    guest = room.add_remote_participant(FakeRemoteParticipant("guest"))

    assert caller_participant(room) is guest
    stamped = room.add_remote_participant(FakeRemoteParticipant("caller", attributes={"lkap.tz": "UTC"}))
    assert caller_participant(room) is stamped
    del avatar


# ------------------------------------------------------------------ clock times


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("14:30", time(14, 30)),
        ("9:05", time(9, 5)),
        ("2:30 pm", time(14, 30)),
        ("12 am", time(0, 0)),
        ("12pm", time(12, 0)),
        ("9a.m.", time(9, 0)),
        ("23:59:30", time(23, 59, 30)),
        ("24:00", None),
        ("13 pm", None),
        ("noon", None),
        ("", None),
    ],
)
def test_parse_clock_time(text: str, expected: time | None) -> None:
    assert parse_clock_time(text) == expected
