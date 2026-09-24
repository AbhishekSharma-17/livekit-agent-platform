---
name: test_agent
description: Run a text test-chat session against an agent before publishing it.
arguments: agent
concepts: sessions-and-test-chat
recipes: test-and-publish
---
Test agent "{agent}" in chat before it goes live. Start a chat, send at
least two realistic turns for what this agent is meant to handle, and
report what came back — including any tool calls or knowledge-base hits you
saw in the events. If `chat_start` returns `code="no_worker"`, tell the
user rather than retrying blindly; follow `next_steps`. Only suggest
publishing once `agent_validate` is clean and the test read the way it
should.
