# EchoVoiice Assistant

A voice app for people who speak **Ewe**. It has three parts:

- **Talk**: have a spoken conversation in Ewe with an assistant that can talk about anything. A hospital mode turns it into a reception guide that also writes a French summary for staff.
- **Translate**: a face-to-face interpreter between someone who speaks only Ewe and someone who speaks English or French.
- **Learn**: type a word in English, French or Ewe and see it in Ewe, hear how it sounds, and use it in a sentence.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026), theme **Build for Your World**.

## How it works

```
Someone speaks Ewe
  -> Meta MMS, two models in parallel             speech to Ewe text
       (everyday Ewe + formal/Bible-style Ewe)
  -> Muse Spark (Meta Model API)                   understands, answers or translates, in Ewe
  -> EchoVoiice TTS (Meta MMS-TTS as fallback)     Ewe text to speech
They hear the reply in Ewe
```

All language work goes through the Muse Spark API: it reads and writes Ewe directly, so there is no separate translation model to download or run. Only speech recognition and the fallback voice run locally.

Ewe is spoken in different ways: everyday Ewe, often mixed with French, and a more formal Ewe like the Bible's. One speech model rarely handles both, so the app runs two Meta MMS models on every Ewe recording. [`waxal-benchmarking/mms-300m-waxal-ewe`](https://huggingface.co/waxal-benchmarking/mms-300m-waxal-ewe) is MMS fine-tuned on everyday conversational Ewe (licence CC BY-NC 4.0). `facebook/mms-1b-all` with its Ewe adapter was trained mostly on Bible readings. Muse Spark sees both versions and works out what the person meant. Change the list with `ASR_MODELS`.

For the Translate page, `facebook/mms-1b-all` also hears English and French by switching to its English or French adapter. When it is already loaded for Ewe the same copy is shared, so this needs no extra memory. English and French replies are spoken by the browser's own voices.

## Talk

Two assistants:

- **Chat** (the default) talks about anything, like a general assistant: greetings, small talk, questions on any subject.
- **Hospital** is an interpreter and guide at a hospital entrance, not a medical tool. It helps patients describe what they feel, explains where departments are and what happens next, and repeats staff instructions in Ewe. It never diagnoses, never suggests medicine, and never judges how serious something is. If a patient describes an urgent sign, it tells them to alert staff right away and flags the summary as urgent. Hospital facts come only from [`app/data/hospital.json`](app/data/hospital.json), which the hospital edits. Staff see a French summary under **Staff**.

Two ways to talk:

- **Live**: a hands-free conversation. The browser keeps listening, notices when you stop talking (a pause of about 1.6 seconds), and sends what you said. While the assistant answers you can just talk to interrupt it.
- **Tap to talk**: tap to start, tap again to send.

You can also type in Ewe.

## Translate

Choose English or Français at the top; Ewe is always the other side. The pairs are Ewe ⇄ English and Ewe ⇄ French; English ⇄ French is not offered.

Each person has a side with a microphone and a text box. With **Start live interpreting** the app listens hands-free: the highlighted side speaks, the translation is spoken to the other person, and then the turn passes to them. Tap a side to give it the turn. The microphone stays off while a translation plays, so the app does not translate its own voice. Without live mode, tap your side's microphone to talk and tap again to stop.

## Learn

Type a word or short phrase in English, French or Ewe (for an Ewe word, choose whether to see it in English or French). The lesson shows the Ewe in large type, a simple pronunciation, an example sentence with its translation, and a short tip. The ▶ buttons play the Ewe word and the example; the English or French plays in the browser's voice. Recent words stay on the page.

## What is new in this hackathon

Everything in this repository was written during the hackathon:

- the conversation loop and session memory (`app/conversation.py`) and the prompts (`app/prompts/`)
- speech recognition with Meta MMS: two Ewe models merged by Muse Spark, plus English and French adapters (`app/asr.py`)
- translation and word lessons with Muse Spark (`app/language.py`)
- the Muse Spark client (`app/llm.py`)
- the API (`app/main.py`) and the mobile web frontend in Next.js (`web/`)

**Built before the hackathon:** [EchoVoiice](https://github.com/jeankpoti/echoVoiice), my Ewe text-to-speech API (FastAPI + Coqui TTS). This project calls it over HTTP as an external dependency and does not include its code. If it is unreachable, Meta's MMS-TTS Ewe model (`facebook/mms-tts-ewe`) speaks instead.

## Try it in one step

You need [Docker](https://www.docker.com/products/docker-desktop/) and a Meta Model API key.

```bash
git clone https://github.com/jeankpoti/echovoiice-assistant.git
cd echovoiice-assistant
cp .env.example .env        # put your Meta Model API key in MODEL_API_KEY
docker compose up --build
```

Open http://localhost:3001.

This starts three services:

| Service | Port | What it is |
| --- | --- | --- |
| `web` | 3001 | The Next.js frontend |
| `assistant` | 8080 | The API (this repository); interactive docs at http://localhost:8080/docs |
| `echovoiice` | inside Docker only | The pre-existing EchoVoiice TTS API, built unmodified straight from [its GitHub repository](https://github.com/jeankpoti/echoVoiice), so there is nothing else to clone. The assistant creates its own EchoVoiice account and API key on first use |

The first start downloads the Meta speech models (about 4 GB) and takes several minutes. They are kept in a Docker volume, so later starts are quick. Give Docker at least 8 GB of memory.

On Apple Silicon Macs, EchoVoiice may crash while generating speech (a PyTorch segmentation fault on ARM). The app then uses the MMS-TTS voice and skips EchoVoiice for `ECHOVOIICE_RETRY_SECONDS`; on x86 servers EchoVoiice is used normally.

## Run it without Docker

You need Python 3.10 or newer and Node.js 20 or newer. The Meta speech models download from Hugging Face on first use (about 4 GB).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then put your Meta Model API key in MODEL_API_KEY
uvicorn app.main:app --reload --port 8080
```

Start EchoVoiice separately (it uses port 8000 by default). Then either put an EchoVoiice API key in `ECHOVOIICE_API_KEY`, or set `ECHOVOIICE_ACCOUNT_EMAIL` and `ECHOVOIICE_ACCOUNT_PASSWORD` and the assistant creates an account and key itself.

Then start the frontend. It forwards `/v1/*` requests to the API (`API_URL`, default `http://localhost:8080`):

```bash
cd web
npm install
npm run dev                 # http://localhost:3001
```

To use it on a phone, the page needs HTTPS for the microphone, for example through `ngrok http 3001`.

## Configuration

Settings come from environment variables or `.env` (see [`.env.example`](.env.example)).

| Variable | Default | What it does |
| --- | --- | --- |
| `MODEL_API_KEY` | (required) | Meta Model API key for Muse Spark |
| `META_API_BASE` | `https://api.meta.ai/v1` | Meta Model API address |
| `META_MODEL` | `muse-spark-1.3` | Model used for conversation, translation and lessons |
| `META_REASONING_EFFORT` | `minimal` | `minimal` answers about twice as fast as `low` |
| `ASR_MODELS` | `waxal-benchmarking/mms-300m-waxal-ewe,facebook/mms-1b-all` | Ewe speech models, comma-separated; all run on every recording |
| `ASR_LANGUAGE` | `ewe` | Language adapter for multilingual MMS models |
| `MULTILINGUAL_ASR_MODEL` | `facebook/mms-1b-all` | Hears English and French on the Translate page |
| `ECHOVOIICE_URL` | `http://localhost:8000` | EchoVoiice TTS API |
| `ECHOVOIICE_API_KEY` | | EchoVoiice key; or leave empty and set the two account variables below |
| `ECHOVOIICE_ACCOUNT_EMAIL`, `ECHOVOIICE_ACCOUNT_PASSWORD` | | Account the assistant signs in with (and creates) to get its own key |
| `TTS_SPEED` | `1.1` | EchoVoiice speaking speed, 0.5 to 2.0 |
| `USE_TTS_FALLBACK` | `true` | Speak with Meta MMS-TTS when EchoVoiice fails |
| `TTS_FALLBACK_MODEL` | `facebook/mms-tts-ewe` | The fallback voice |
| `ECHOVOIICE_RETRY_SECONDS` | `300` | After EchoVoiice fails, use the fallback straight away for this long |
| `DEFAULT_MODE` | `general` | Assistant for new Talk sessions: `general` or `hospital` |
| `MAX_HISTORY_TURNS` | `12` | Earlier exchanges the assistant remembers |
| `FACILITY_FILE` | `app/data/hospital.json` | Hospital facts for the hospital guide |
| `PRELOAD_MODELS` | `false` | Load and warm up the speech models at startup (Docker Compose turns this on) |

## API

| Endpoint | What it does |
| --- | --- |
| `POST /v1/sessions` | Starts a Talk conversation. Optional JSON `{"mode": "general" \| "hospital"}`. Returns a session id and the spoken Ewe greeting |
| `POST /v1/converse` | Form fields `session_id` and `audio` (WAV). Returns what was heard, the Ewe reply, the reply audio (base64 WAV) and, in hospital mode, the staff summary |
| `POST /v1/converse/text` | JSON `{"session_id", "text"}`; the same with typed Ewe |
| `GET /v1/sessions/{id}/summary` | French summary for staff, urgent flag, department |
| `POST /v1/translate` | JSON `text`, `source`, `target` (one of them `ewe`, the other `eng` or `fra`). Returns the translation, with Ewe audio when translating into Ewe |
| `POST /v1/translate/audio` | Form fields `source`, `target` and `audio` (WAV). Same, starting from speech |
| `POST /v1/learn` | JSON `text`, `source` (`eng`, `fra` or `ewe`) and, for Ewe words, `other` (`eng` or `fra`). Returns the Ewe, its meaning, pronunciation, an example and audio |
| `GET /health` | Configuration status |

The server logs how long each step takes (`Heard ... (asr=...)` and `Timing: llm=... tts=...`).

## Tests

```bash
pytest
```

The tests replace the models and the APIs with fakes, so they run offline in under a second.
