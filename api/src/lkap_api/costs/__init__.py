"""Session cost and the per-minute estimate (PLAN-V2 V2-12, docs/v4/COSTS.md).

* `costs/service.py` — actual cost: the write paths (`cost_session`,
  `add_egress_cost_line`) and the read path (`render_cost`, with the
  estimate-vs-actual `drivers`).
* `costs/estimate.py` — the pure per-minute estimator (`build_estimate`).
* `costs/assumptions.py` — the usage model's defaults and workspace averages.
* `costs/prices.py` — workspace prices, the cached OpenRouter sheet, `PriceBook`.
* `costs/snapshot.py` — `attach_estimate`, the creation-time snapshot.
* `costs/mapping.py` — why a usage entry is priced by its pipeline slot.
"""

from __future__ import annotations

from lkap_api.costs.estimate import QuoteFn, build_estimate, estimated_usd_for, template_estimate
from lkap_api.costs.prices import PriceBook, load_price_book
from lkap_api.costs.service import (
    ComputedLine,
    CostContext,
    add_egress_cost_line,
    compute_usage_lines,
    config_for_session,
    cost_context,
    cost_session,
    estimate_channel,
    infra_lines,
    price_line,
    render_cost,
)

__all__ = [
    "ComputedLine",
    "CostContext",
    "PriceBook",
    "QuoteFn",
    "add_egress_cost_line",
    "build_estimate",
    "compute_usage_lines",
    "config_for_session",
    "cost_context",
    "cost_session",
    "estimate_channel",
    "estimated_usd_for",
    "infra_lines",
    "load_price_book",
    "price_line",
    "render_cost",
    "template_estimate",
]
