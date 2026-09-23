#!/usr/bin/env python3
"""A local `webhook.site`-style listener for LKAP v2 outbound webhooks.

Used by PLAN-V2 stage L7 ("`session.ended` webhook signed + delivered; QA
scored within 60s"): point a webhook endpoint's URL at this script
(`http://127.0.0.1:8899/` in dev — `LKAP_ENV=dev` allows `http://` webhook
URLs, see `routers/webhooks.py::_check_url`), end a real test call, and watch
the event print with its signature verified.

Stdlib only (`http.server`), so it needs no venv: any Python 3.9+ works.

Usage:
    python3 scripts/webhook_sink.py <secret> [--port 8899]

`<secret>` is the plaintext signing secret shown once when the webhook
endpoint was created (`POST /v1/webhooks` → `WebhookEndpointCreated.secret`).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIGNATURE_HEADER = "X-LKAP-Signature"
EVENT_ID_HEADER = "X-LKAP-Event-Id"

#: Mirrors `lkap_api.webhooks.signing.DEFAULT_TOLERANCE_S` — kept independent
#: (stdlib-only script) rather than importing the api package.
DEFAULT_TOLERANCE_S = 300


def verify_signature(secret: str, header: str, body: bytes, *, tolerance_s: int = DEFAULT_TOLERANCE_S) -> tuple[bool, str]:
    """Re-implements `lkap_api.webhooks.signing.verify_signature` (CONTRACTS-V2 §4.6).

    Returns:
        `(ok, reason)` — `reason` explains a failure for the printed output.
    """
    try:
        parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    except ValueError:
        return False, "malformed signature header"
    t_raw, v1 = parts.get("t"), parts.get("v1")
    if t_raw is None or v1 is None:
        return False, "missing t= or v1= in signature header"
    try:
        t = int(t_raw)
    except ValueError:
        return False, f"non-integer timestamp: {t_raw!r}"
    if abs(time.time() - t) > tolerance_s:
        return False, f"timestamp outside {tolerance_s}s tolerance"
    signed = f"{t}.{body.decode('utf-8')}".encode()
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        return False, "signature mismatch (wrong secret, or the body was altered in transit)"
    return True, "ok"


def make_handler(secret: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming convention
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            signature_header = self.headers.get(SIGNATURE_HEADER, "")
            event_id = self.headers.get(EVENT_ID_HEADER, "")
            ok, reason = verify_signature(secret, signature_header, body)

            status_line = "VALID" if ok else "INVALID"
            print(f"\n=== webhook received: signature {status_line} ({reason}) ===", flush=True)
            print(f"event id: {event_id}", flush=True)
            try:
                pretty = json.dumps(json.loads(body), indent=2, sort_keys=True)
            except ValueError:
                pretty = body.decode("utf-8", errors="replace")
            print(pretty, flush=True)

            self.send_response(200 if ok else 401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib's signature
            pass  # keep stdout for the parsed event only, not the raw HTTP log line

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("secret", help="the webhook endpoint's plaintext signing secret")
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.secret))
    print(f"webhook_sink listening on http://127.0.0.1:{args.port}/ (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
