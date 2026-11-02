"""EchoVoiice Assistant API: talk with an assistant in Ewe, translate Ewe <-> English/French, learn Ewe words."""
import asyncio
import base64
import logging
import time
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .asr import EweRecognizer
from .config import settings
from . import language
from .conversation import GREETING_EWE, GREETING_FR, Assistant, Turn
from .llm import LLMError, MuseSparkClient
from .tts import EchoVoiiceClient, MMSTTS, Speaker, TTSError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 10 * 1024 * 1024


class TextTurnRequest(BaseModel):
    session_id: str
    text: str = Field(..., min_length=1, max_length=2000)


class TurnResponse(BaseModel):
    session_id: str
    transcript: str
    transcript_alternatives: list[str] = []
    reply_ewe: str
    staff_summary_fr: str
    urgent: bool
    department: Optional[str] = None
    audio_wav_base64: Optional[str] = None
    voice: Optional[str] = None
    tts_error: Optional[str] = None


class SessionRequest(BaseModel):
    mode: Optional[Literal["general", "hospital"]] = None


class SessionResponse(BaseModel):
    session_id: str
    mode: str
    greeting_ewe: str
    greeting_fr: str
    audio_wav_base64: Optional[str] = None
    voice: Optional[str] = None
    tts_error: Optional[str] = None


class SummaryResponse(BaseModel):
    session_id: str
    staff_summary_fr: str
    urgent: bool
    department: Optional[str] = None
    turns: int


Language = Literal["ewe", "eng", "fra"]


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    source: Language
    target: Language


class TranslateResponse(BaseModel):
    source: Language
    target: Language
    text: str
    text_alternatives: list[str] = []
    translation: str
    # Ewe audio when translating into Ewe; English and French are spoken by the browser
    audio_wav_base64: Optional[str] = None
    voice: Optional[str] = None
    tts_error: Optional[str] = None


class LearnRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200)
    source: Language
    # The learner's language; taken from `source` when that is English or French
    other: Optional[Literal["eng", "fra"]] = None


class LearnResponse(BaseModel):
    source: Language
    other: Literal["eng", "fra"]
    text: str
    ewe: str
    other_text: str
    pronunciation: str
    example_ewe: str
    example_other: str
    note: str
    audio_word_base64: Optional[str] = None
    audio_example_base64: Optional[str] = None
    tts_error: Optional[str] = None


def create_app(
    assistant: Optional[Assistant] = None,
    recognizer=None,
    speaker: Optional[Speaker] = None,
) -> FastAPI:
    if assistant is None:
        assistant = Assistant(MuseSparkClient())
    if recognizer is None:
        recognizer = EweRecognizer()
    if speaker is None:
        speaker = Speaker(EchoVoiiceClient(), MMSTTS() if settings.use_tts_fallback else None)

    def preload() -> None:
        if hasattr(recognizer, "load"):
            recognizer.load()
        if hasattr(recognizer, "warm_up"):
            recognizer.warm_up()
        if getattr(speaker, "fallback", None) is not None:
            speaker.fallback.synthesize("Woezɔ")  # load and warm up the fallback voice

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.preload_models:
            logger.info("Preloading the speech models")
            await asyncio.to_thread(preload)
        yield

    app = FastAPI(
        lifespan=lifespan,
        title="EchoVoiice Assistant",
        description="A voice assistant patients can talk to in Ewe: Meta MMS hears them, "
        "Muse Spark answers, EchoVoiice speaks back.",
        version="0.1.0",
    )

    async def speak(text: str) -> dict:
        try:
            audio, voice = await speaker.speak(text)
            return {"audio_wav_base64": base64.b64encode(audio).decode(), "voice": voice}
        except Exception as e:  # the text reply is still useful without audio
            logger.exception("Speech synthesis failed")
            return {"tts_error": str(e)}

    def session_or_404(session_id: str):
        session = assistant.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Unknown session. Start one with POST /v1/sessions.")
        return session

    async def run_turn(session, transcript: str, alternatives: Optional[list[str]] = None) -> TurnResponse:
        started = time.perf_counter()
        try:
            turn: Turn = await assistant.respond(session, transcript, alternatives)
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        replied = time.perf_counter()
        voice = await speak(turn.reply_ewe)
        logger.info("Timing: llm=%.1fs tts=%.1fs", replied - started, time.perf_counter() - replied)
        return TurnResponse(
            session_id=session.id,
            transcript=transcript,
            transcript_alternatives=alternatives or [],
            reply_ewe=turn.reply_ewe,
            staff_summary_fr=turn.staff_summary_fr,
            urgent=turn.urgent,
            department=turn.department,
            **voice,
        )

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "llm_model": settings.meta_model,
            "llm_key_set": bool(settings.meta_api_key),
            "asr_models": settings.asr_models,
            "echovoiice_url": settings.echovoiice_url,
            "sessions": len(assistant.sessions),
        }

    @app.post("/v1/sessions", response_model=SessionResponse)
    async def start_session(request: Optional[SessionRequest] = None):
        session = assistant.start_session(request.mode if request else None)
        return SessionResponse(
            session_id=session.id,
            mode=session.mode,
            greeting_ewe=GREETING_EWE,
            greeting_fr=GREETING_FR,
            **(await speak(GREETING_EWE)),
        )

    async def hear(audio: UploadFile, lang: str = "ewe") -> list[str]:
        """Transcribe an upload. Ewe may come back in several versions, one per speech model."""
        data = await audio.read()
        if not data:
            raise HTTPException(status_code=400, detail="Empty audio upload.")
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio is larger than 10 MB.")
        started = time.perf_counter()
        try:
            if lang == "ewe":
                transcripts = await asyncio.to_thread(recognizer.transcribe_all, data)
            else:
                text = await asyncio.to_thread(recognizer.transcribe_in, data, lang)
                transcripts = [text] if text else []
        except Exception as e:
            logger.exception("Speech recognition failed")
            raise HTTPException(status_code=400, detail=f"Could not read the audio: {e}") from e
        if not transcripts:
            raise HTTPException(status_code=422, detail="No speech was recognized. Please try again.")
        logger.info("Heard (%s): %s (asr=%.1fs)", lang, " | ".join(transcripts), time.perf_counter() - started)
        return transcripts

    @app.post("/v1/converse", response_model=TurnResponse)
    async def converse(session_id: str = Form(...), audio: UploadFile = File(...)):
        session = session_or_404(session_id)
        transcripts = await hear(audio)
        return await run_turn(session, transcripts[0], transcripts[1:])

    @app.post("/v1/converse/text", response_model=TurnResponse)
    async def converse_text(request: TextTurnRequest):
        session = session_or_404(request.session_id)
        return await run_turn(session, request.text.strip())

    async def run_translation(
        text: str, source: str, target: str, alternatives: Optional[list[str]] = None
    ) -> TranslateResponse:
        try:
            translation = await language.translate(assistant.llm, text, source, target, alternatives)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        voice = await speak(translation) if target == "ewe" else {}
        return TranslateResponse(
            source=source, target=target, text=text, text_alternatives=alternatives or [],
            translation=translation, **voice,
        )

    @app.post("/v1/translate", response_model=TranslateResponse)
    async def translate_text(request: TranslateRequest):
        return await run_translation(request.text.strip(), request.source, request.target)

    @app.post("/v1/translate/audio", response_model=TranslateResponse)
    async def translate_audio(
        source: Language = Form(...), target: Language = Form(...), audio: UploadFile = File(...)
    ):
        try:
            language.check_pair(source, target)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        transcripts = await hear(audio, source)
        return await run_translation(transcripts[0], source, target, transcripts[1:])

    @app.post("/v1/learn", response_model=LearnResponse)
    async def learn(request: LearnRequest):
        other = request.source if request.source != "ewe" else (request.other or "eng")
        text = request.text.strip()
        try:
            data = await language.lesson(assistant.llm, text, request.source, other)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        async def voice_of(text: str) -> dict:
            return await speak(text) if text else {}

        word, example = await asyncio.gather(voice_of(data["ewe"]), voice_of(data["example_ewe"]))
        return LearnResponse(
            source=request.source, other=other, text=text,
            ewe=data["ewe"], other_text=data["other"], pronunciation=data["pronunciation"],
            example_ewe=data["example_ewe"], example_other=data["example_other"], note=data["note"],
            audio_word_base64=word.get("audio_wav_base64"),
            audio_example_base64=example.get("audio_wav_base64"),
            tts_error=word.get("tts_error") or example.get("tts_error"),
        )

    @app.get("/v1/sessions/{session_id}/summary", response_model=SummaryResponse)
    async def summary(session_id: str):
        session = session_or_404(session_id)
        return SummaryResponse(
            session_id=session.id,
            staff_summary_fr=session.staff_summary_fr,
            urgent=session.urgent,
            department=session.department,
            turns=len(session.turns),
        )

    return app


app = create_app()
