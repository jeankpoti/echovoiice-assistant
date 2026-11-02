"""Settings, read from environment variables (and a local .env file)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # Muse Spark through the Meta Model API (OpenAI-compatible chat completions)
    meta_api_key: str = field(default_factory=lambda: os.getenv("MODEL_API_KEY", ""))
    meta_api_base: str = field(default_factory=lambda: os.getenv("META_API_BASE", "https://api.meta.ai/v1"))
    meta_model: str = field(default_factory=lambda: os.getenv("META_MODEL", "muse-spark-1.3"))
    meta_reasoning_effort: str = field(default_factory=lambda: os.getenv("META_REASONING_EFFORT", "minimal"))

    # Ewe speech recognition (Meta MMS), comma-separated; every model hears each recording.
    # waxal-benchmarking/mms-300m-waxal-ewe is MMS fine-tuned on everyday conversational Ewe
    # (WAXAL corpus; model licence CC BY-NC 4.0, fine for a non-commercial demo). On WAXAL test
    # recordings it got 34% of words wrong alone, against 40% when combined with facebook/mms-1b-all
    # (trained mostly on Bible readings), so it runs alone by default.
    asr_models: list = field(
        default_factory=lambda: [
            m.strip()
            for m in os.getenv("ASR_MODELS", "waxal-benchmarking/mms-300m-waxal-ewe").split(",")
            if m.strip()
        ]
    )
    # Language adapter, used only by multilingual MMS checkpoints such as mms-1b-all
    asr_language: str = field(default_factory=lambda: os.getenv("ASR_LANGUAGE", "ewe"))
    # English and French speech on the Translate page: Muse Voice Transcribe on the Meta Model API
    voice_transcribe_model: str = field(
        default_factory=lambda: os.getenv("VOICE_TRANSCRIBE_MODEL", "muse-voice-transcribe-1.0")
    )

    # Text-to-speech: the existing EchoVoiice API, with Meta MMS-TTS as fallback
    echovoiice_url: str = field(default_factory=lambda: os.getenv("ECHOVOIICE_URL", "http://localhost:8000"))
    echovoiice_api_key: str = field(default_factory=lambda: os.getenv("ECHOVOIICE_API_KEY", ""))
    # Without an API key, the assistant signs in to EchoVoiice with this account (created on first use)
    echovoiice_account_email: str = field(default_factory=lambda: os.getenv("ECHOVOIICE_ACCOUNT_EMAIL", ""))
    echovoiice_account_password: str = field(default_factory=lambda: os.getenv("ECHOVOIICE_ACCOUNT_PASSWORD", ""))
    tts_fallback_model: str = field(default_factory=lambda: os.getenv("TTS_FALLBACK_MODEL", "facebook/mms-tts-ewe"))
    # EchoVoiice speaking speed (0.5 to 2.0). Its voice learned from slow Bible readings;
    # a little faster sounds more like everyday talk.
    tts_speed: float = field(default_factory=lambda: float(os.getenv("TTS_SPEED", "1.1")))
    # After EchoVoiice fails, use the fallback voice straight away for this many seconds
    echovoiice_retry_seconds: float = field(default_factory=lambda: float(os.getenv("ECHOVOIICE_RETRY_SECONDS", "300")))
    use_tts_fallback: bool = field(default_factory=lambda: _bool("USE_TTS_FALLBACK", True))

    # Conversation mode for new sessions: "general" (talk about anything) or "hospital"
    default_mode: str = field(default_factory=lambda: os.getenv("DEFAULT_MODE", "general"))

    # Facility facts the assistant may repeat (departments, hours, steps)
    facility_file: Path = field(
        default_factory=lambda: Path(os.getenv("FACILITY_FILE", str(BASE_DIR / "data" / "hospital.json")))
    )
    # Load the local Meta models at startup instead of on the first request
    preload_models: bool = field(default_factory=lambda: _bool("PRELOAD_MODELS", False))
    max_history_turns: int = field(default_factory=lambda: int(os.getenv("MAX_HISTORY_TURNS", "12")))


settings = Settings()
