---
name: add_knowledge
description: Add a knowledge base to an existing agent, from text, a file or a url.
arguments: agent
concepts: knowledge
recipes: knowledge-from-text
---
Add knowledge to agent "{agent}". Ask the user for the content (pasted
text, a local file path, or a url) and a short name for the knowledge base.
Use `wait=true` so you see the final ingest status before telling the user
it's ready, and try one `kb_search` query yourself to sanity-check it
before attaching it to the agent.
