"""Save-time checks of an agent's telephony destinations (rulings R-V2-21, R-V2-23).

:func:`telephony_issues` is registered into ``config_service.VALIDATORS`` (never
by editing ``config_service`` or ``flows/validation.py``); importing
:mod:`lkap_api.telephony` registers it. It reports, as ``error`` issues:

* a transfer-destination label used twice (case-insensitive) or blank, at
  ``telephony.transfer_targets[i].label``;
* every ``config.telephony.transfer_targets[i].to`` and every flow
  ``transfer`` node's ``flow.nodes[i].to`` that the workspace's dialing policy
  refuses (:func:`lkap_api.telephony.policy.destination_problem`).

The policy check runs only when the context carries a policy
(``ValidationContext.telephony_policy``, loaded by ``validation_context_for``);
a policy-less workspace loads the default-deny policy, so every destination is
refused until an admin lists the allowed prefixes.
"""

from __future__ import annotations

from lkap_contracts.api_models import Issue
from lkap_contracts.flow import TransferNode

from lkap_api.config_service import ValidationContext, register_validator
from lkap_api.telephony.policy import destination_problem

__all__ = ["telephony_issues"]


def telephony_issues(ctx: ValidationContext) -> list[Issue]:
    """Label uniqueness and dialing-policy findings for ``ctx.config`` (registered validator)."""
    issues: list[Issue] = []
    targets = ctx.config.telephony.transfer_targets
    seen: dict[str, int] = {}
    for index, target in enumerate(targets):
        key = target.label.strip().casefold()
        path = f"telephony.transfer_targets[{index}].label"
        if not key:
            issues.append(Issue(path=path, message="a transfer destination needs a name", severity="error"))
        elif key in seen:
            issues.append(
                Issue(
                    path=path,
                    message=f"'{target.label}' is already the name of destination {seen[key] + 1}; "
                    "names must be unique",
                    severity="error",
                )
            )
        else:
            seen[key] = index

    policy = ctx.telephony_policy
    if policy is None:
        return issues
    for index, target in enumerate(targets):
        problem = destination_problem(policy, target.to)
        if problem is not None:
            issues.append(
                Issue(path=f"telephony.transfer_targets[{index}].to", message=problem, severity="error")
            )
    flow = ctx.config.flow
    if flow is not None:
        for index, node in enumerate(flow.nodes):
            if isinstance(node, TransferNode):
                problem = destination_problem(policy, node.to)
                if problem is not None:
                    issues.append(Issue(path=f"flow.nodes[{index}].to", message=problem, severity="error"))
    return issues


register_validator(telephony_issues)
