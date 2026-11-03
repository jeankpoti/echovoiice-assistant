"""Muse Spark client for the Meta Model API.

The API speaks the OpenAI chat completions format:
POST {base}/chat/completions with a bearer key, reply in choices[0].message.content.
"""
import asyncio
import json
import logging
from typing import AsyncIterator, Dict, List, Protocol

import httpx

from .config import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class ChatModel(Protocol):
    async def chat(self, messages: List[Dict[str, str]]) -> str: ...

    def stream(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]: ...


class MuseSparkClient:
    def __init__(
        self,
        api_key: str = settings.meta_api_key,
        base_url: str = settings.meta_api_base,
        model: str = settings.meta_model,
        reasoning_effort: str = settings.meta_reasoning_effort,
        timeout: float = settings.llm_timeout,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.transport = transport
        self._client: httpx.AsyncClient | None = None
        self._client_loop = None

    def _http(self) -> httpx.AsyncClient:
        """One connection to the Meta Model API, kept open: setting up a new secure
        connection for every call added about 0.6 s each time."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._client_loop is not loop:
            self._client = httpx.AsyncClient(timeout=self.timeout, transport=self.transport)
            self._client_loop = loop
        return self._client

    async def chat(self, messages: List[Dict[str, str]]) -> str:
        if not self.api_key:
            raise LLMError("MODEL_API_KEY is not set. Add it to your .env file.")

        payload = {"model": self.model, "messages": messages}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        # Now and then the API is very slow or answers with no text at all; a second try
        # usually comes back normally, so one retry beats waiting
        problem = ""
        for attempt in range(2):
            try:
                response = await self._http().post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
            except httpx.TimeoutException:
                problem = f"Meta Model API did not answer within {self.timeout:.0f} seconds."
                logger.warning("%s (attempt %d)", problem, attempt + 1)
                continue
            if response.status_code != 200:
                raise LLMError(f"Meta Model API returned {response.status_code}: {response.text[:300]}")

            try:
                choice = response.json()["choices"][0]
                content = choice["message"].get("content")
            except (KeyError, IndexError, ValueError, AttributeError) as e:
                raise LLMError(f"Unexpected Meta Model API response: {response.text[:300]}") from e
            if isinstance(content, str) and content.strip():
                return content
            problem = f"Meta Model API returned an empty reply (finish_reason={choice.get('finish_reason')})."
            logger.warning("%s (attempt %d): %s", problem, attempt + 1, response.text[:500])

        raise LLMError(f"{problem} Please try again.")

    async def stream(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        """Yield the reply in pieces as Muse Spark writes it (server-sent events)."""
        if not self.api_key:
            raise LLMError("MODEL_API_KEY is not set. Add it to your .env file.")

        payload = {"model": self.model, "messages": messages, "stream": True}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        problem = ""
        for attempt in range(2):
            started = False
            try:
                async with self._http().stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode(errors="replace")
                        raise LLMError(f"Meta Model API returned {response.status_code}: {body[:300]}")
                    async for line in response.aiter_lines():
                        data = line.strip()[5:].strip() if line.startswith("data:") else ""
                        if not data or data == "[DONE]":
                            continue
                        try:
                            choices = json.loads(data).get("choices") or []
                        except ValueError:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content") if choices else None
                        if delta:
                            started = True
                            yield delta
            except httpx.TimeoutException:
                if started:
                    raise LLMError("Meta Model API stopped answering partway through. Please try again.")
                problem = f"Meta Model API did not answer within {self.timeout:.0f} seconds."
                logger.warning("%s (attempt %d)", problem, attempt + 1)
                continue
            if started:
                return
            # Nothing came back; one retry usually gets a real reply
            problem = "Meta Model API returned an empty reply."
            logger.warning("%s (attempt %d)", problem, attempt + 1)

        raise LLMError(f"{problem} Please try again.")
