---
name: connect_livekit
description: Connect a LiveKit Cloud project or self-hosted server as a new connection.
arguments:
concepts: connections-and-pools
recipes: connect-livekit
---
Connect a LiveKit project. Ask the user for the url and whether they'd
rather paste the key and secret directly or point you at an env file
(`file:/path#KEY`) — both are fine, see the recipe below. Use
`test_first=true` so a bad credential is caught before it's saved, and ask
before setting `is_default=true` if the workspace already has a default
connection.
