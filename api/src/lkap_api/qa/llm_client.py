"""`JudgeLLM`: the interface the QA scorer calls to get a raw completion.

**Open design question, reported rather than decided** (see the V2-08
report): the registry (`contracts/src/lkap_contracts/providers.py`) records
what a *worker* plugin class needs to construct a provider, not what wire
protocol the **api process** should speak to reach it directly over HTTP —
every provider is normally invoked from inside a `livekit-agents` plugin
object running in the worker, which the api does not depend on (that
separation is exactly why `slim`/`full` worker images exist). Nothing in
CONTRACTS-V2 or ARCHITECTURE-V2 says how the api reaches a provider's HTTP
endpoint for QA scoring.

This ships :class:`OpenAiCompatibleJudgeLLM` — the wire format used by
`openai-llm` and any BYO "OpenAI-compatible endpoint" credential (`base_url` +
bearer secret is a documented `FieldSpec` pattern already in the registry) —
because it is the one provider family whose HTTP shape is a documented,
verifiable public API. It is **not** wired up for `livekit-inference-llm`
(the pack default, `requires_credential=False`): the LiveKit Inference
Gateway's HTTP endpoint and auth scheme are not documented anywhere in this
repo, so `qa.resolve.resolve_judge` explicitly reports that provider as
unsupported (`error="unsupported judge provider: ..."`) rather than guessing
at an endpoint that could silently fail or hit the wrong host in production.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class JudgeLLM(Protocol):
    """Something that can turn `(system, user)` messages into a text completion."""

    async def complete(self, *, system: str, user: str) -> str:
        """Return the model's raw text completion for one `(system, user)` turn."""
        ...


class OpenAiCompatibleJudgeLLM:
    """Calls an OpenAI chat-completions-shaped `POST {base_url}/chat/completions`."""

    def __init__(
        self, http: httpx.AsyncClient, *, base_url: str, api_key: str, model: str, temperature: float = 0.0
    ) -> None:
        """Build the client.

        Args:
            http: The shared outbound HTTP client (mocked with `respx` in tests).
            base_url: The provider's OpenAI-compatible base URL, no trailing slash
                required (e.g. `https://api.openai.com/v1`).
            api_key: Sent as `Authorization: Bearer <api_key>`.
            model: The model id to request.
            temperature: Sampling temperature; 0 for a deterministic judge by default.
        """
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature

    async def complete(self, *, system: str, user: str) -> str:
        """POST the chat completion request and return the assistant message text."""
        response = await self._http.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json={
                "model": self._model,
                "temperature": self._temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        response.raise_for_status()
        data = response.json()
        content: str = data["choices"][0]["message"]["content"]
        return content
