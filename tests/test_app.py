import asyncio
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

    async def stream(self, messages):
        reply = await self.chat(messages)
        for i in range(0, len(reply), 7):  # in small pieces, like the real API
            yield reply[i : i + 7]


class FakeRecognizer:
    def transcribe_all(self, data):
        return ["Ta le veve nam", "Ta le vevem nam"]


class FakeTranscriber:
    """Muse Voice Transcribe stand-in; `heard` is what it returns for English and French."""

    def __init__(self, heard=None):
        self.heard = heard if heard is not None else {"eng": "Where is the market?", "fra": "Où est le marché ?"}
        self.calls = []

    async def transcribe(self, data, language):
        self.calls.append(language)
        return self.heard[language]


class FakeSpeaker:
    async def speak(self, text):
        return WAV, "fake"


def reply(text, summary="Le patient a mal à la tête.", urgent=False, department=None):
    return json.dumps(
        {"reply": text, "staff_summary_fr": summary, "urgent": urgent, "department": department}
    )


def make_client(llm, transcriber=None):
    assistant = Assistant(llm)
    app = create_app(
        assistant=assistant, recognizer=FakeRecognizer(), speaker=FakeSpeaker(),
        transcriber=transcriber or FakeTranscriber(),
    )
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
async def test_muse_spark_client_retries_after_a_timeout():
    calls = []

    def handler(request: httpx.Request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Woezɔ"}}]})

    client = MuseSparkClient(api_key="k", transport=httpx.MockTransport(handler))
    assert await client.chat([{"role": "user", "content": "hi"}]) == "Woezɔ"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_muse_spark_client_gives_up_after_two_timeouts():
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("slow", request=request)

    client = MuseSparkClient(api_key="k", timeout=5, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="within 5 seconds"):
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


def test_ewe_reference_reaches_every_prompt_that_reads_ewe():
    from app.conversation import build_system_prompt
    from app.language import _translate_messages, detect_and_translate, lesson

    sheet = "Everyday Ewe reference"
    for mode in ("general", "hospital"):
        assert sheet in build_system_prompt(mode)
    for source, target in (("ewe", "eng"), ("fra", "ewe")):
        system = _translate_messages("Dɔ le wuyem", source, target)[0]["content"]
        assert sheet in system and "silently repair" in system
        assert "Add words and phrases" not in system  # the editing note stays out

    llm = FakeLLM(["ewe\nI'm hungry", json.dumps({"ewe": "akpa", "other": "fish"})])
    asyncio.run(detect_and_translate(llm, ["dɔ le wuyem"], "", "eng"))
    asyncio.run(lesson(llm, "fish", "eng", "eng"))
    assert all(sheet in call[0]["content"] for call in llm.calls)


def test_lexicon_replaces_only_whole_messages(tmp_path, monkeypatch):
    from app import ewe_guides
    from app.language import _translate_messages

    lexicon = tmp_path / "ewe_lexicon.csv"
    lexicon.write_text(
        "﻿Standard_Ewe,User_Input_Variation,Variation_Type,English_Translation,Dialect_Note,Status\n"
        "Meɖe kuku,Mede kuku,Orthography,Please,,ok\n"
        "Woezɔ,Woezor,Phonetic Text,Welcome,,ok\n"
        "Ʋu,Wu,Phonetic Text,Vehicle,,check\n"
        "Akpe,Akpe na wò,Phrase Extension,Thank you,,ok\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ewe_guides, "LEXICON_FILE", lexicon)

    assert ewe_guides.normalize_whole("medekuku") == "Meɖe kuku"
    assert ewe_guides.normalize_whole("Woezor!") == "Woezɔ"
    assert ewe_guides.normalize_whole("wu") is None  # marked for checking
    assert ewe_guides.normalize_whole("dɔ le wuyem") is None  # never inside a sentence
    assert ewe_guides.normalize_whole("Akpe na wò") is None  # already correct Ewe

    messages = _translate_messages("mede kuku", "ewe", "eng")
    assert messages[1]["content"] == "Meɖe kuku"
    assert '"Mede kuku" = Meɖe kuku (Please)' in messages[0]["content"]
    assert '"Wu"' not in messages[0]["content"]


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


def post_audio(client, **fields):
    return client.post("/v1/translate/audio", data=fields, files={"audio": ("a.wav", wav_bytes(), "audio/wav")})


def test_translate_audio_with_a_fixed_language():
    llm = FakeLLM(["Afi ka asie le?", "My head hurts"])
    transcriber = FakeTranscriber()
    client = make_client(llm, transcriber)
    french = post_audio(client, other="fra", source="fra").json()
    assert french["text"] == "Où est le marché ?"  # heard by Muse Voice Transcribe
    assert french["source"] == "fra" and french["target"] == "ewe"
    ewe = post_audio(client, other="eng", source="ewe").json()
    assert ewe["text"] == "Ta le veve nam"  # heard by the Ewe models
    assert ewe["target"] == "eng"
    assert ewe["text_alternatives"] == ["Ta le vevem nam"]
    assert "Ta le vevem nam" in llm.calls[1][-1]["content"]  # every version reaches the model
    assert transcriber.calls == ["fra"]


def test_translate_audio_detects_ewe_automatically():
    llm = FakeLLM(["ewe\nJ'ai mal à la tête"])
    client = make_client(llm, FakeTranscriber({"fra": "ta lé vévé nam"}))
    data = post_audio(client, other="fra").json()  # source defaults to auto
    assert data["source"] == "ewe" and data["target"] == "fra"
    assert data["translation"] == "J'ai mal à la tête"
    assert data["audio_wav_base64"] is None  # French is spoken by the browser
    sent = llm.calls[0][-1]["content"]
    assert "Ta le veve nam" in sent and "ta lé vévé nam" in sent  # Muse Spark sees both recognizers


def test_translate_audio_detects_french_automatically_and_speaks_ewe():
    client = make_client(FakeLLM(["fra\nAfi ka asie le?"]))
    data = post_audio(client, other="fra").json()
    assert data["source"] == "fra" and data["target"] == "ewe"
    assert base64.b64decode(data["audio_wav_base64"]) == WAV


def test_translate_audio_skips_detection_when_only_ewe_was_heard():
    llm = FakeLLM(["Where is the market?"])
    client = make_client(llm, FakeTranscriber({"eng": ""}))
    data = post_audio(client, other="eng").json()
    assert data["source"] == "ewe" and data["translation"] == "Where is the market?"
    assert "Translate the message from Ewe" in llm.calls[0][0]["content"]


def test_translate_audio_rejects_a_language_outside_the_pair():
    client = make_client(FakeLLM([]))
    assert post_audio(client, other="eng", source="fra").status_code == 422


@pytest.mark.asyncio
async def test_muse_voice_transcribe_request_shape():
    from app.transcribe import MuseVoiceTranscriber

    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"transcript": "Bonjour", "turns": []})

    transcriber = MuseVoiceTranscriber(api_key="k", base_url="https://api.meta.ai/v1",
                                       transport=httpx.MockTransport(handler))
    assert await transcriber.transcribe(wav_bytes(), "fra") == "Bonjour"
    assert seen["url"] == "https://api.meta.ai/v1/asr/transcribe"
    assert b'"languageBias": ["French"]' in seen["body"]
    assert b"muse-voice-transcribe-1.0" in seen["body"]


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


# --- Streaming: sentence by sentence ---

async def pieces_of(text, size=5):
    for i in range(0, len(text), size):
        yield text[i : i + size]


@pytest.mark.asyncio
async def test_sentences_are_cut_as_the_text_streams():
    from app.streaming import sentences

    text = "Ee! Mele edzi nyuie. Wò hã, aleke nèle egbe? Mèle dɔwɔƒe le Lomé hã a? Akpe."
    got = [s async for s in sentences(pieces_of(text))]
    assert got[0] == "Ee! Mele edzi nyuie."  # "Ee!" alone is too short to speak first
    assert "".join(got).replace(" ", "") == text.replace(" ", "")
    assert all(not s.startswith(" ") for s in got)


@pytest.mark.asyncio
async def test_spoken_sentences_keep_their_order():
    from app.streaming import spoken_sentences

    async def voice(text):
        await asyncio.sleep(0.01 if "first" in text else 0)
        return {"audio_wav_base64": text.upper()}

    text = "This is the first sentence. And this is the second, a bit longer one. Third one here."
    got = [s async for s in spoken_sentences(pieces_of(text), voice)]
    assert [s["text"].upper() for s in got] == [s["audio_wav_base64"] for s in got]
    assert got[0]["text"].startswith("This is the first")


def stream_events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_converse_stream_speaks_each_sentence():
    reply = "Ŋdi na wò hã! Mele edzi nyuie, akpe. Wò hã, aleke nèle egbe? Aleke dɔwɔwɔ le yiyim?"
    llm = FakeLLM([reply])
    client = make_client(llm)
    session_id = client.post("/v1/sessions").json()["session_id"]
    events = stream_events(client.post("/v1/converse/stream", data={"session_id": session_id, "text": "Ŋdi"}))
    assert events[0] == {"type": "heard", "text": "Ŋdi", "alternatives": []}
    sentences = [e for e in events if e["type"] == "sentence"]
    assert len(sentences) >= 2
    assert all(base64.b64decode(e["audio_wav_base64"]) == WAV for e in sentences)
    assert events[-1]["type"] == "done" and events[-1]["text"] == reply
    # The turn is remembered like a normal one
    assert client.get(f"/v1/sessions/{session_id}/summary").json()["turns"] == 1


def test_converse_stream_with_audio_and_hospital_json():
    llm = FakeLLM([reply("Yi consultation. Woawɔ kpekpeɖeŋu na wò.", department="Consultation")])
    client = make_client(llm)
    session_id = client.post("/v1/sessions", json={"mode": "hospital"}).json()["session_id"]
    events = stream_events(client.post(
        "/v1/converse/stream", data={"session_id": session_id},
        files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
    ))
    assert events[0]["text"] == "Ta le veve nam"
    spoken = " ".join(e["text"] for e in events if e["type"] == "sentence")
    assert spoken == "Yi consultation. Woawɔ kpekpeɖeŋu na wò."  # the JSON is never read aloud
    assert events[-1]["department"] == "Consultation"


def test_translate_stream_detects_and_speaks_ewe():
    client = make_client(FakeLLM(["fra\nAfi ka asie le? Medi be maƒle nu aɖewo."]))
    events = stream_events(client.post(
        "/v1/translate/stream", data={"other": "fra"}, files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
    ))
    assert events[0] == {"type": "heard", "text": "Où est le marché ?", "alternatives": [],
                         "source": "fra", "target": "ewe"}
    sentences = [e for e in events if e["type"] == "sentence"]
    assert sentences[0]["text"] == "Afi ka asie le?" and sentences[0]["audio_wav_base64"]
    assert events[-1]["text"] == "Afi ka asie le? Medi be maƒle nu aɖewo."


def test_translate_stream_typed_text_into_english_has_no_server_audio():
    client = make_client(FakeLLM(["Where is the market?"]))
    events = stream_events(client.post(
        "/v1/translate/stream", data={"other": "eng", "source": "ewe", "text": "Afi ka asie le?"},
    ))
    assert events[0]["target"] == "eng"
    assert events[1] == {"type": "sentence", "text": "Where is the market?"}


def test_translate_stream_needs_a_language_for_typed_text():
    client = make_client(FakeLLM([]))
    assert client.post("/v1/translate/stream", data={"other": "eng", "text": "hi"}).status_code == 422


def test_stream_reports_model_errors_as_an_event():
    class Broken(FakeLLM):
        async def stream(self, messages):
            raise LLMError("down")
            yield  # pragma: no cover

    client = make_client(Broken([]))
    events = stream_events(client.post(
        "/v1/translate/stream", data={"other": "eng", "source": "eng", "text": "hello"},
    ))
    assert events[-1] == {"type": "error", "detail": "down"}


@pytest.mark.asyncio
async def test_muse_spark_client_streams_pieces():
    body = "".join(
        f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n" for piece in ["Woe", "zɔ!"]
    ) + "data: [DONE]\n\n"

    def handler(request: httpx.Request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    client = MuseSparkClient(api_key="k", transport=httpx.MockTransport(handler))
    assert [p async for p in client.stream([{"role": "user", "content": "hi"}])] == ["Woe", "zɔ!"]


@pytest.mark.asyncio
async def test_speaker_waits_longer_after_each_echovoiice_failure():
    def handler(request):
        raise httpx.ConnectError("refused")

    class Fallback:
        def synthesize(self, text):
            return b"mms"

    client = EchoVoiiceClient(base_url="http://tts", api_key="elt_x", transport=httpx.MockTransport(handler))
    speaker = Speaker(client, Fallback(), retry_seconds=60)
    await speaker.speak("a")
    first_wait = speaker.primary_down_until
    speaker.primary_down_until = 0
    await speaker.speak("b")
    assert speaker.primary_failures == 2
    assert speaker.primary_down_until - first_wait > 50  # about twice as long the second time
