import base64
import io
import json

import httpx
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.asr import load_audio
from app.conversation import Assistant, parse_reply
from app.llm import LLMError, MuseSparkClient
from app.main import create_app
from app.tts import EchoVoiiceClient, Speaker

WAV = b"RIFF-fake-wav"


def wav_bytes(rate=48_000, seconds=1.0):
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(int(rate * seconds), dtype=np.float32), rate, format="WAV")
    return buffer.getvalue()


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


class FakeRecognizer:
    def transcribe_all(self, data):
        return ["Ta le veve nam", "Ta le vevem nam"]

    def transcribe_in(self, data, language):
        return {"eng": "where is the market", "fra": "où est le marché"}[language]


class FakeSpeaker:
    async def speak(self, text):
        return WAV, "fake"


def reply(text, summary="Le patient a mal à la tête.", urgent=False, department=None):
    return json.dumps(
        {"reply": text, "staff_summary_fr": summary, "urgent": urgent, "department": department}
    )


def make_client(llm):
    assistant = Assistant(llm)
    app = create_app(assistant=assistant, recognizer=FakeRecognizer(), speaker=FakeSpeaker())
    return TestClient(app)


def test_parse_reply_reads_json_inside_text():
    data = parse_reply('Sure: {"reply": "Woezɔ", "urgent": true}')
    assert data["reply"] == "Woezɔ"
    assert data["urgent"] is True


def test_parse_reply_falls_back_to_plain_text():
    assert parse_reply("Woezɔ")["reply"] == "Woezɔ"


def test_session_greets_with_audio():
    client = make_client(FakeLLM([]))
    data = client.post("/v1/sessions").json()
    assert data["session_id"]
    assert base64.b64decode(data["audio_wav_base64"]) == WAV


def test_voice_turn_runs_full_loop_and_keeps_memory():
    llm = FakeLLM([reply("Míegbɔ!"), reply("Yi consultation.", department="General consultation")])
    client = make_client(llm)
    session_id = client.post("/v1/sessions", json={"mode": "hospital"}).json()["session_id"]

    first = client.post(
        "/v1/converse", data={"session_id": session_id}, files={"audio": ("a.wav", wav_bytes(), "audio/wav")}
    ).json()
    assert first["transcript"] == "Ta le veve nam"
    assert first["transcript_alternatives"] == ["Ta le vevem nam"]
    assert "Version B: Ta le vevem nam" in llm.calls[0][-1]["content"]
    assert first["reply_ewe"] == "Míegbɔ!"
    assert first["voice"] == "fake"

    second = client.post("/v1/converse/text", json={"session_id": session_id, "text": "Afi kae?"}).json()
    assert second["department"] == "General consultation"
    # The second call sees the system prompt, both earlier messages and the new one
    assert len(llm.calls[1]) == 4
    assert llm.calls[1][0]["role"] == "system"
    assert "Never diagnose" in llm.calls[1][0]["content"]

    summary = client.get(f"/v1/sessions/{session_id}/summary").json()
    assert summary["turns"] == 2
    assert summary["staff_summary_fr"] == "Le patient a mal à la tête."


def test_general_mode_is_default_and_takes_plain_text():
    llm = FakeLLM(["Woezɔ! Ŋdi na wò. Aleke nèle?"])
    client = make_client(llm)
    session = client.post("/v1/sessions").json()
    assert session["mode"] == "general"
    data = client.post("/v1/converse/text", json={"session_id": session["session_id"], "text": "Ŋdi"}).json()
    assert data["reply_ewe"] == "Woezɔ! Ŋdi na wò. Aleke nèle?"
    system = llm.calls[0][0]["content"]
    assert "conversation about anything" in system
    assert "Never diagnose" not in system


def test_unknown_mode_is_rejected():
    client = make_client(FakeLLM([]))
    assert client.post("/v1/sessions", json={"mode": "pirate"}).status_code == 422


def test_urgent_flag_stays_on():
    llm = FakeLLM([reply("Yi emergency!", urgent=True), reply("Ɛ̃")])
    client = make_client(llm)
    session_id = client.post("/v1/sessions").json()["session_id"]
    client.post("/v1/converse/text", json={"session_id": session_id, "text": "Ʋu le dodom"})
    second = client.post("/v1/converse/text", json={"session_id": session_id, "text": "Ɛ̃"}).json()
    assert second["urgent"] is True


def test_unknown_session_is_404():
    client = make_client(FakeLLM([]))
    response = client.post("/v1/converse/text", json={"session_id": "nope", "text": "Hi"})
    assert response.status_code == 404


def test_llm_error_is_502():
    class Broken:
        async def chat(self, messages):
            raise LLMError("down")

    client = make_client(Broken())
    session_id = client.post("/v1/sessions").json()["session_id"]
    response = client.post("/v1/converse/text", json={"session_id": session_id, "text": "Hi"})
    assert response.status_code == 502


def test_load_audio_resamples_to_16k_mono():
    audio = load_audio(wav_bytes(rate=48_000, seconds=1.0))
    assert len(audio) == 16_000


@pytest.mark.asyncio
async def test_muse_spark_client_request_shape():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    client = MuseSparkClient(api_key="k", base_url="https://api.meta.ai/v1", model="muse-spark-1.3",
                             transport=httpx.MockTransport(handler))
    assert await client.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert seen["url"] == "https://api.meta.ai/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "muse-spark-1.3"


@pytest.mark.asyncio
async def test_muse_spark_client_retries_empty_reply():
    replies = iter([None, "Woezɔ"])

    def handler(request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}, "finish_reason": "stop"}]})

    client = MuseSparkClient(api_key="k", transport=httpx.MockTransport(handler))
    assert await client.chat([{"role": "user", "content": "hi"}]) == "Woezɔ"


@pytest.mark.asyncio
async def test_muse_spark_client_empty_reply_is_llm_error():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "length"}]})

    client = MuseSparkClient(api_key="k", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="empty reply"):
        await client.chat([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_muse_spark_client_needs_key():
    with pytest.raises(LLMError):
        await MuseSparkClient(api_key="").chat([])


@pytest.mark.asyncio
async def test_echovoiice_client_generates_then_downloads():
    def handler(request: httpx.Request):
        if request.url.path == "/api/v1/tts/generate":
            assert json.loads(request.content)["language"] == "ewe"
            return httpx.Response(200, json={"download_url": "/api/v1/audio/abc"})
        assert request.url.path == "/api/v1/audio/abc"
        return httpx.Response(200, content=WAV)

    client = EchoVoiiceClient(base_url="http://tts", api_key="elt_x", transport=httpx.MockTransport(handler))
    assert await client.synthesize("Woezɔ") == WAV


@pytest.mark.asyncio
async def test_speaker_falls_back_when_echovoiice_is_down():
    def handler(request):
        raise httpx.ConnectError("refused")

    class Fallback:
        def synthesize(self, text):
            return b"mms"

    speaker = Speaker(EchoVoiiceClient(base_url="http://tts", transport=httpx.MockTransport(handler)), Fallback())
    assert await speaker.speak("Woezɔ") == (b"mms", "mms-tts")


@pytest.mark.asyncio
async def test_speaker_skips_echovoiice_for_a_while_after_it_fails():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        raise httpx.ConnectError("refused")

    class Fallback:
        def synthesize(self, text):
            return b"mms"

    client = EchoVoiiceClient(base_url="http://tts", api_key="elt_x", transport=httpx.MockTransport(handler))
    speaker = Speaker(client, Fallback(), retry_seconds=60)
    await speaker.speak("Woezɔ")
    await speaker.speak("Ŋdi")
    assert len(calls) == 1  # the second reply did not wait on EchoVoiice

    speaker.primary_down_until = 0  # retry window over
    await speaker.speak("Ŋdi")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_echovoiice_client_gets_its_own_api_key():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/auth/signin":
            return httpx.Response(401, json={"detail": "Incorrect email or password"})
        if request.url.path == "/api/v1/auth/signup":
            body = json.loads(request.content)
            assert body["username"] == "assistant"
            return httpx.Response(201, json={"access_token": "jwt"})
        if request.url.path == "/api/v1/auth/regenerate-api-key":
            assert request.headers["authorization"] == "Bearer jwt"
            return httpx.Response(200, json={"api_key": "elt_new"})
        if request.url.path == "/api/v1/tts/generate":
            assert request.headers["authorization"] == "Bearer elt_new"
            return httpx.Response(200, json={"download_url": "/api/v1/audio/abc"})
        return httpx.Response(200, content=WAV)

    client = EchoVoiiceClient(
        base_url="http://tts", api_key="", account_email="assistant@echovoiice.local",
        account_password="Demo-Pass-2026", transport=httpx.MockTransport(handler),
    )
    assert await client.synthesize("Woezɔ") == WAV
    assert await client.synthesize("Woezɔ") == WAV
    assert calls.count("/api/v1/auth/signup") == 1


def test_ewe_recognizer_keeps_distinct_transcripts_from_each_model():
    from app.asr import EweRecognizer

    class Model:
        def __init__(self, name, text):
            self.model_name, self.text = name, text

        def transcribe_audio(self, audio):
            if self.text is None:
                raise RuntimeError("boom")
            return self.text

    recognizer = EweRecognizer([Model("a", "Woezɔ"), Model("b", "Woezɔ"), Model("c", None), Model("d", "Woezo")])
    assert recognizer.transcribe_all(wav_bytes()) == ["Woezɔ", "Woezo"]


def test_language_adapter_only_for_multilingual_mms():
    from app.asr import MMSRecognizer

    assert MMSRecognizer("facebook/mms-1b-all").language == "ewe"
    assert MMSRecognizer("waxal-benchmarking/mms-300m-waxal-ewe").language is None


def test_everyday_ewe_style_guide_is_in_every_mode():
    from app.conversation import build_system_prompt

    for mode in ("general", "hospital"):
        prompt = build_system_prompt(mode)
        assert "How your Ewe should sound" in prompt
        assert "Add real examples below" not in prompt  # the editing note stays out


def test_translate_text_into_ewe_comes_with_ewe_audio():
    llm = FakeLLM(["Afi ka asie le?"])
    client = make_client(llm)
    data = client.post("/v1/translate", json={"text": "Where is the market?", "source": "eng", "target": "ewe"}).json()
    assert data["translation"] == "Afi ka asie le?"
    assert base64.b64decode(data["audio_wav_base64"]) == WAV
    system = llm.calls[0][0]["content"]
    assert "from English into Ewe" in system
    assert "Never answer the message" in system


def test_translate_from_ewe_to_french_has_no_server_audio():
    client = make_client(FakeLLM(["Où est le marché ?"]))
    data = client.post("/v1/translate", json={"text": "Afi ka asie le?", "source": "ewe", "target": "fra"}).json()
    assert data["translation"] == "Où est le marché ?"
    assert data["audio_wav_base64"] is None  # the browser speaks French and English


def test_translate_refuses_english_french_and_same_language():
    client = make_client(FakeLLM([]))
    for source, target in (("eng", "fra"), ("fra", "eng"), ("ewe", "ewe")):
        response = client.post("/v1/translate", json={"text": "Hello", "source": source, "target": target})
        assert response.status_code == 422


def test_translate_audio_hears_each_language():
    llm = FakeLLM(["Afi ka asie le?", "Where is the market?"])
    client = make_client(llm)
    french = client.post(
        "/v1/translate/audio", data={"source": "fra", "target": "ewe"},
        files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
    ).json()
    assert french["text"] == "où est le marché"
    ewe = client.post(
        "/v1/translate/audio", data={"source": "ewe", "target": "eng"},
        files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
    ).json()
    assert ewe["text"] == "Ta le veve nam"
    assert ewe["text_alternatives"] == ["Ta le vevem nam"]
    assert "Ta le vevem nam" in llm.calls[1][-1]["content"]  # every version reaches the model


def test_learn_word_gives_ewe_with_audio():
    lesson = {
        "ewe": "asi", "other": "market", "pronunciation": "ah-see",
        "example_ewe": "Meyi asi me.", "example_other": "I went to the market.", "note": "Also means hand.",
    }
    client = make_client(FakeLLM([json.dumps(lesson)]))
    data = client.post("/v1/learn", json={"text": "market", "source": "eng"}).json()
    assert data["other"] == "eng"
    assert data["ewe"] == "asi" and data["other_text"] == "market"
    assert data["audio_word_base64"] and data["audio_example_base64"]


def test_learn_from_ewe_needs_a_readable_lesson():
    client = make_client(FakeLLM(["I am not sure."]))
    response = client.post("/v1/learn", json={"text": "asi", "source": "ewe", "other": "fra"})
    assert response.status_code == 502
