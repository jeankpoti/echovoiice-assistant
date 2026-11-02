"""Ewe speech recognition with Meta MMS (Massively Multilingual Speech).

The default model is MMS fine-tuned on everyday conversational Ewe. Multilingual MMS
checkpoints such as facebook/mms-1b-all also work, with their Ewe adapter.
"""
import io
import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf

from .config import settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


def load_audio(data: bytes) -> np.ndarray:
    """Decode WAV/FLAC/OGG bytes to mono float32 at 16 kHz."""
    audio, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if rate != SAMPLE_RATE and len(audio):
        duration = len(audio) / rate
        target = np.linspace(0, duration, int(duration * SAMPLE_RATE), endpoint=False)
        source = np.arange(len(audio)) / rate
        audio = np.interp(target, source, audio).astype(np.float32)
    return audio


def uses_language_adapter(model_name: str) -> bool:
    """Multilingual MMS checkpoints (mms-1b-all, mms-1b-l1107, ...) need a language adapter."""
    return "mms-1b" in model_name


class MMSRecognizer:
    def __init__(self, model_name: str, language: str = settings.asr_language):
        self.model_name = model_name
        self.language = language if uses_language_adapter(model_name) else None
        self.processor = None
        self.model = None

    def load(self) -> None:
        if self.model is not None:
            return
        import torch  # noqa: F401  (heavy imports stay out of tests)
        from transformers import AutoModelForCTC, AutoProcessor

        if self.language:
            logger.info("Loading %s with the %s adapter", self.model_name, self.language)
            self.processor = AutoProcessor.from_pretrained(self.model_name, target_lang=self.language)
            self.model = AutoModelForCTC.from_pretrained(
                self.model_name, target_lang=self.language, ignore_mismatched_sizes=True
            )
        else:
            logger.info("Loading %s", self.model_name)
            self.processor = AutoProcessor.from_pretrained(self.model_name)
            self.model = AutoModelForCTC.from_pretrained(self.model_name)
        self.model.eval()

    def transcribe_audio(self, audio) -> str:
        import torch

        self.load()
        inputs = self.processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt")
        with torch.no_grad():
            logits = self.model(**inputs).logits
        ids = torch.argmax(logits, dim=-1)[0]
        return self.processor.decode(ids).strip()


class EweRecognizer:
    """Runs one or more Ewe speech models on the same audio.

    With several models (ASR_MODELS), every version goes to Muse Spark, which works out
    the meaning. One model is the default: see the note on asr_models in config.py.
    """

    def __init__(self, models=None):
        self.models = models if models is not None else [MMSRecognizer(name) for name in settings.asr_models]

    def load(self) -> None:
        for model in self.models:
            model.load()

    def warm_up(self) -> None:
        """The first pass through a model is much slower; do it now rather than on someone's first turn."""
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        for model in self.models:
            model.transcribe_audio(silence)

    def transcribe_all(self, data: bytes) -> list[str]:
        audio = load_audio(data)
        if len(audio) < SAMPLE_RATE // 4:
            return []
        def run(model):
            try:
                return model.transcribe_audio(audio)
            except Exception:
                logger.exception("%s failed", model.model_name)
                return None

        # The models run side by side; results keep the configured order
        with ThreadPoolExecutor(max_workers=len(self.models) or 1) as pool:
            results = list(pool.map(run, self.models))
        transcripts = []
        for text in results:
            if text and text not in transcripts:
                transcripts.append(text)
        return transcripts
