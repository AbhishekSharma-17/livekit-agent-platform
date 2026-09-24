---
name: build_agent
description: Build a new agent of a given kind (insurance intake, or a plain assistant) with the given name.
arguments: kind, name
concepts: agents, pipeline-modes
recipes: insurance-intake-agent, generic-assistant
---
Build an agent named "{name}" of kind "{kind}".

If `{kind}` is about insurance, claims or first-notice-of-loss, follow the
"insurance-intake-agent" recipe below (`pack_id="insurance_claim"`).
Otherwise follow "generic-assistant" (`pack_id="generic"`). Call `me()`
first if you have not already this session. After `agent_create`, always
`agent_validate` before telling the user it's ready, and offer to test it
in chat before publishing.
