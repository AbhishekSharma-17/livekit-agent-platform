"""Session cost computation: `pricing.PRICES` x real usage (PLAN-V2 V2-12, CONTRACTS-V2 §4.2).

See `costs/service.py` for the write paths (`cost_session`, `add_egress_cost_line`)
and read path (`render_cost`), and `costs/mapping.py` for why a usage entry's
provider is resolved from the session's pipeline slot rather than the vendor
plugin's own free-text `.provider` string.
"""

from __future__ import annotations

from lkap_api.costs.service import (
    ComputedLine,
    add_egress_cost_line,
    compute_usage_lines,
    config_for_session,
    cost_session,
    price_line,
    render_cost,
)

__all__ = [
    "ComputedLine",
    "add_egress_cost_line",
    "compute_usage_lines",
    "config_for_session",
    "cost_session",
    "price_line",
    "render_cost",
]
