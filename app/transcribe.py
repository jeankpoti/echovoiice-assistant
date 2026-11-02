"""English and French speech to text with Muse Voice Transcribe on the Meta Model API.

POST {base}/asr/transcribe, multipart: a JSON `request` part and a WAV `audio` part
(mono 16-bit PCM at 16 kHz). Ewe is not one of its languages; Ewe goes through MMS.
"""
import io
import json

import httpx
import soundfile as sf

from .asr import SAMPLE_RATE, load_audio
from .config import settings
from .llm import LLMError

LANGUAGE_NAMES = {"eng": "English", "fra": "French"}


class MuseVoiceTranscriber:
    def __init__(
        self,
        api_key: str = settings.meta_api_key,
        base_url: str = settings.meta_api_base,
        model: str = settings.voice_transcribe_model,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.transport = transport

    async def transcribe(self, data: bytes, language: str) -> str:
        """Transcribe English (eng) or French (fra) speech; empty when nothing was said."""
        if not self.api_key:
            raise LLMError("MODEL_API_KEY is not set. Add it to your .env file.")
        audio = load_audio(data)
        if len(audio) < SAMPLE_RATE // 4:
            return ""
        wav = io.BytesIO()
        sf.write(wav, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        request = {"model": self.model, "audioEncoding": "WAV", "languageBias": [LANGUAGE_NAMES[language]]}

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            response = await client.post(
                f"{self.base_url}/asr/transcribe",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={
                    "request": (None, json.dumps(request), "application/json"),
                    "audio": ("speech.wav", wav.getvalue(), "audio/wav"),
                },
            )
        if response.status_code != 200:
            raise LLMError(f"Muse Voice Transcribe returned {response.status_code}: {response.text[:300]}")
        try:
            return (response.json().get("transcript") or "").strip()
        except ValueError as e:
            raise LLMError(f"Unexpected Muse Voice Transcribe response: {response.text[:300]}") from e
