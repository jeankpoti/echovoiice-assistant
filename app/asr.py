"""Ewe speech recognition with Meta MMS (Massively Multilingual Speech).

facebook/mms-1b-all is one CTC model with a small adapter per language;
the Ewe adapter is loaded once at startup.
"""
import io
import logging
import threading
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
        self.active_language = self.language
        self.processor = None
        self.model = None
        # Switching the language adapter changes the model in place, so one call at a time
        self.lock = threading.Lock()

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

    def _use_language(self, language: str) -> None:
        if language == self.active_language:
            return
        logger.info("Switching %s to the %s adapter", self.model_name, language)
        self.processor.tokenizer.set_target_lang(language)
        self.model.load_adapter(language)
        self.active_language = language

    def transcribe_audio(self, audio, language: str | None = None) -> str:
        """Transcribe in `language` (an MMS code such as ewe, eng or fra); multilingual models only."""
        import torch

        self.load()
        with self.lock:
            if self.language:
                self._use_language(language or self.language)
            elif language and language != settings.asr_language:
                raise ValueError(f"{self.model_name} only knows one language")
            inputs = self.processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt")
            with torch.no_grad():
                logits = self.model(**inputs).logits
            ids = torch.argmax(logits, dim=-1)[0]
            return self.processor.decode(ids).strip()


class EweRecognizer:
    """Runs one or more Ewe speech models on the same audio.

    Everyday spoken Ewe and formal, Bible-style Ewe sound different, and each model is
    better at one of them, so every version goes to Muse Spark, which works out the meaning.
    """

    def __init__(self, models=None):
        self.models = models if models is not None else [MMSRecognizer(name) for name in settings.asr_models]
        self._multilingual = None

    def multilingual(self) -> MMSRecognizer:
        """The model that also hears English and French: shared with the Ewe ones when possible."""
        if self._multilingual is None:
            shared = [m for m in self.models if getattr(m, "model_name", None) == settings.multilingual_asr_model]
            self._multilingual = shared[0] if shared else MMSRecognizer(settings.multilingual_asr_model)
        return self._multilingual

    def transcribe_in(self, data: bytes, language: str) -> str:
        """Transcribe English (eng) or French (fra) speech for the Translate page."""
        audio = load_audio(data)
        if len(audio) < SAMPLE_RATE // 4:
            return ""
        return self.multilingual().transcribe_audio(audio, language)

    def load(self) -> None:
        for model in self.models:
            model.load()

    def warm_up(self) -> None:
        """The first pass through a model is much slower; do it now rather than on someone's first turn."""
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        for model in self.models:
            model.transcribe_audio(silence)
        # Fetch the English and French adapters now, if their model is already loaded for Ewe
        shared = self.multilingual()
        if shared in self.models:
            for language in ("eng", "fra"):
                shared.transcribe_audio(silence, language)
            shared.transcribe_audio(silence)  # back to Ewe

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
