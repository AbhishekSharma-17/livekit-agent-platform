"""Unit tests for small helpers in `lkap_api.main`, exercised without a full ASGI lifespan.

The `app`/`client` fixtures in `conftest.py` build the FastAPI app via
`create_app()` but never actually run its `lifespan` (they set `app.state.db`
directly instead of letting `_startup` do it), so `_log_worker_callback_url`
is never reached through those fixtures. It's called directly here instead —
exactly the "small helper a test can caplog directly" the docstring asks for.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from conftest import captured_text

from lkap_api.logging import configure_logging
from lkap_api.settings import Settings

# `lkap_api.main` builds a module-level `app = create_app()` at import time
# (the uvicorn entrypoint), which needs real settings env vars — deferred
# into each test, after the `settings` fixture's `monkeypatch.setenv` calls
# have run, exactly like `conftest.py`'s own `app` fixture does.


@pytest.fixture
def plain_log_capture(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> Iterator[pytest.LogCaptureFixture]:
    """Attach pytest's log handler without going through the `app`/lifespan fixtures.

    `lkap_api.main` builds a module-level `app = create_app()` (the uvicorn
    entrypoint) which calls `configure_logging()` itself on first import — if
    that import happens *after* this fixture attaches `caplog.handler` to the
    root logger, `configure_logging`'s `root_logger.handlers = [handler]`
    (`logging.py`) silently drops it again. Importing here, before our own
    `configure_logging()` call, guarantees our attachment is the last one.
    """
    import lkap_api.main  # noqa: F401,PLC0415 - see docstring; triggers its module-level create_app()

    configure_logging(level="DEBUG", json_output=False)
    root = logging.getLogger()
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        yield caplog
    finally:
        root.removeHandler(caplog.handler)


def test_log_worker_callback_url_warns_when_derived_from_port(
    settings: Settings, plain_log_capture: pytest.LogCaptureFixture
) -> None:
    from lkap_api.main import _log_worker_callback_url  # noqa: PLC0415 - needs settings env vars set first

    settings.api_base_url = None
    settings.public_base_url = None
    settings.port = 8096

    _log_worker_callback_url(settings)

    text = captured_text(plain_log_capture)
    assert "worker_callback_url_derived_from_port" in text
    assert "8096" in text


def test_log_worker_callback_url_is_plain_info_when_explicit(
    settings: Settings, plain_log_capture: pytest.LogCaptureFixture
) -> None:
    from lkap_api.main import _log_worker_callback_url  # noqa: PLC0415 - needs settings env vars set first

    settings.api_base_url = "http://scratch-api.internal:8096"

    _log_worker_callback_url(settings)

    text = captured_text(plain_log_capture)
    assert "worker_callback_url_derived_from_port" not in text
    assert "worker_callback_url" in text
    assert "scratch-api.internal" in text


def test_log_worker_callback_url_treats_public_base_url_as_explicit(
    settings: Settings, plain_log_capture: pytest.LogCaptureFixture
) -> None:
    from lkap_api.main import _log_worker_callback_url  # noqa: PLC0415 - needs settings env vars set first

    settings.api_base_url = None
    settings.public_base_url = "https://api.example.com"

    _log_worker_callback_url(settings)

    text = captured_text(plain_log_capture)
    assert "worker_callback_url_derived_from_port" not in text
