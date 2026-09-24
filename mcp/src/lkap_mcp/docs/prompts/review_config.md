---
name: review_config
description: Review an agent's full configuration for common mistakes before it ships.
arguments: agent
concepts: agents, roles-and-scopes
recipes:
---
Review agent "{agent}" end to end: `agent_get(include_config=true,
include_validation=true)`, then check the pipeline has every required slot
for its mode, the knowledge bases and tools it's attached to actually
exist and resolve, the panel's blocks validate, and (if it dials out) its
`telephony.transfer_targets` are ones the workspace's dialing policy would
actually allow. Report findings as a short list, not a wall of JSON; don't
change anything without asking first.
