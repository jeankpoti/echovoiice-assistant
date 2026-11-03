"""Ewe speech output.

The primary voice is the existing EchoVoiice API (Coqui TTS, Ewe VITS), called over
HTTP as a dependency. If it is unreachable, Meta MMS-TTS for Ewe speaks instead.
"""
import asyncio
import io
import logging
import time

import httpx
import numpy as np
import soundfile as sf

from .config import settings

logger = logging.getLogger(__name__)


class TTSError(RuntimeError):
    pass


class EchoVoiiceClient:
    def __init__(
        self,
        base_url: str = settings.echovoiice_url,
        api_key: str = settings.echovoiice_api_key,
        account_email: str = settings.echovoiice_account_email,
        account_password: str = settings.echovoiice_account_password,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.account_email = account_email
        self.account_password = account_password
        self.timeout = timeout
        self.transport = transport

    async def _provision_key(self, client: httpx.AsyncClient) -> None:
        """Get an EchoVoiice API key with a service account, so no one has to sign up by hand.

        Signs in (or signs up the first time), then asks EchoVoiice for an API key.
        """
        if not (self.account_email and self.account_password):
            raise TTSError("Set ECHOVOIICE_API_KEY, or ECHOVOIICE_ACCOUNT_EMAIL and ECHOVOIICE_ACCOUNT_PASSWORD.")
        credentials = {"email": self.account_email, "password": self.account_password}
        response = await client.post("/api/v1/auth/signin", json=credentials)
        if response.status_code == 401:
            username = self.account_email.split("@")[0].replace(".", "-")
            response = await client.post(
                "/api/v1/auth/signup",
                json={**credentials, "username": username, "full_name": "EchoVoiice Assistant"},
            )
        if response.status_code not in (200, 201):
            raise TTSError(f"EchoVoiice sign-in failed ({response.status_code}): {response.text[:200]}")
        token = response.json()["access_token"]
        response = await client.post(
            "/api/v1/auth/regenerate-api-key", headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code != 200:
            raise TTSError(f"EchoVoiice API key request failed ({response.status_code})")
        self.api_key = response.json()["api_key"]
        logger.info("Got an EchoVoiice API key for %s", self.account_email)

    async def synthesize(self, text: str, speed: float = 1.0) -> bytes:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        ) as client:
            if not self.api_key:
                await self._provision_key(client)
            body = {"text": text, "language": "ewe", "format": "wav", "speed": speed}
            response = await client.post(
                "/api/v1/tts/generate", headers={"Authorization": f"Bearer {self.api_key}"}, json=body
            )
            if response.status_code == 401 and self.account_email:
                await self._provision_key(client)
                response = await client.post(
                    "/api/v1/tts/generate", headers={"Authorization": f"Bearer {self.api_key}"}, json=body
                )
            if response.status_code != 200:
                raise TTSError(f"EchoVoiice returned {response.status_code}: {response.text[:200]}")
            audio = await client.get(response.json()["download_url"])
            if audio.status_code != 200:
                raise TTSError(f"EchoVoiice audio download returned {audio.status_code}")
            return audio.content


class MMSTTS:
    """Fallback voice: facebook/mms-tts-ewe (VITS), run locally."""

    def __init__(self, model_name: str = settings.tts_fallback_model):
        self.model_name = model_name
        self.tokenizer = None
        self.model = None

    def load(self) -> None:
        if self.model is not None:
            return
        from transformers import AutoTokenizer, VitsModel

        logger.info("Loading fallback voice %s", self.model_name)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = VitsModel.from_pretrained(self.model_name)
        self.model.eval()

    def synthesize(self, text: str) -> bytes:
        import torch

        self.load()
        inputs = self.tokenizer(text, return_tensors="pt")
        with torch.no_grad():
            waveform = self.model(**inputs).waveform[0].numpy()
        buffer = io.BytesIO()
        sf.write(buffer, waveform.astype(np.float32), self.model.config.sampling_rate, format="WAV")
        return buffer.getvalue()


class Speaker:
    def __init__(
        self,
        primary: EchoVoiiceClient,
        fallback: MMSTTS | None,
        retry_seconds: float = settings.echovoiice_retry_seconds,
    ):
        self.primary = primary
        self.fallback = fallback
        self.retry_seconds = retry_seconds
        self.primary_down_until = 0.0
        self.primary_failures = 0

    async def speak(self, text: str) -> tuple[bytes, str]:
        """Returns (wav bytes, name of the voice used)."""
        # While EchoVoiice is known to be down, go straight to the fallback rather than
        # waiting for it to fail again on every reply
        if self.fallback is not None and time.monotonic() < self.primary_down_until:
            return await self._fallback(text), "mms-tts"
        try:
            audio = await self.primary.synthesize(text, speed=settings.tts_speed)
            self.primary_failures = 0
            return audio, "echovoiice"
        except (TTSError, httpx.HTTPError) as e:
            if self.fallback is None:
                raise TTSError(str(e)) from e
            # Wait longer after each failure in a row (up to an hour): a voice that keeps
            # failing, like EchoVoiice crashing on Apple Silicon, is not worth a slow try every few minutes
            self.primary_failures += 1
            wait = min(self.retry_seconds * 2 ** (self.primary_failures - 1), 3600)
            self.primary_down_until = time.monotonic() + wait
            logger.warning("EchoVoiice unavailable (%s), using MMS-TTS fallback for the next %.0fs", e, wait)
            return await self._fallback(text), "mms-tts"

    async def _fallback(self, text: str) -> bytes:
        # Off the event loop, so streamed replies keep flowing while it runs
        return await asyncio.to_thread(self.fallback.synthesize, text)
