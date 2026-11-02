# EchoVoiice Assistant

A voice assistant people can talk to in **Ewe**. A patient at a hospital in Togo speaks Ewe, and the assistant answers out loud in Ewe. It helps them explain why they came, tells them where to go, and writes a French summary for the staff.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026), theme **Build for Your World**.

## How it works

```
Patient speaks Ewe
  -> Meta MMS, two models in parallel             speech to Ewe text
       (everyday Ewe + formal/Bible-style Ewe)
  -> Meta NLLB-200 (optional)                      Ewe to French
  -> Muse Spark (Meta Model API)                   understands and answers, keeps the conversation
  -> Meta NLLB-200 (optional)                      French to Ewe
  -> EchoVoiice TTS                                Ewe text to speech
Patient hears the reply in Ewe; staff see a French summary
```

Ewe is spoken in different ways: everyday Ewe, often mixed with French, and a more formal Ewe like the Bible's. One speech model rarely handles both, so the assistant runs two Meta MMS models on every recording. [`waxal-benchmarking/mms-300m-waxal-ewe`](https://huggingface.co/waxal-benchmarking/mms-300m-waxal-ewe) is MMS fine-tuned on everyday conversational Ewe (licence CC BY-NC 4.0). `facebook/mms-1b-all` with its Ewe adapter was trained mostly on Bible readings. Muse Spark sees both versions and works out what the person meant. Change the list with `ASR_MODELS`.

Muse Spark can read and write Ewe directly. If that isn't good enough in testing, set `USE_TRANSLATION=true` and NLLB translates between Ewe and French around it.

## What the assistant does and does not do

It is an interpreter and guide, not a medical tool. It helps patients describe what they feel, explains where departments are and what happens next, and repeats staff instructions in Ewe. It never diagnoses, never suggests medicine, and never judges how serious something is. If a patient describes an urgent sign, it tells them to alert staff right away and flags the summary as urgent. Hospital facts come only from [`app/data/hospital.json`](app/data/hospital.json), which the hospital edits.

## What is new in this hackathon

Everything in this repository was written during the hackathon:

- the conversation loop and session memory (`app/conversation.py`)
- Ewe speech recognition with two Meta MMS models, merged by Muse Spark (`app/asr.py`)
- Ewe and French translation with Meta NLLB-200 (`app/translate.py`)
- the Muse Spark client (`app/llm.py`) and the hospital prompt (`app/prompts/hospital.md`)
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

Open http://localhost:3001 and tap the microphone.

This starts three services. `web` is the Next.js frontend and `assistant` is the API, both from this repository. `echovoiice` is the pre-existing EchoVoiice TTS API, built unmodified straight from [its GitHub repository](https://github.com/jeankpoti/echoVoiice), so there is nothing else to clone. The assistant creates its own EchoVoiice account and API key on first use.

The first start downloads the Meta models (about 4 GB for MMS, plus 2.5 GB for NLLB if translation is on) and takes several minutes. They are kept in a Docker volume, so later starts are quick. Give Docker at least 8 GB of memory.

## Run it without Docker

You need Python 3.10 or newer. The Meta models download from Hugging Face on first use: about 4 GB for MMS and 2.5 GB for NLLB.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then put your Meta Model API key in MODEL_API_KEY
uvicorn app.main:app --reload --port 8080
```

Start EchoVoiice separately (it uses port 8000 by default). Then either put an EchoVoiice API key in `ECHOVOIICE_API_KEY`, or set `ECHOVOIICE_ACCOUNT_EMAIL` and `ECHOVOIICE_ACCOUNT_PASSWORD` and the assistant creates an account and key itself.

Then start the frontend (next section) and open http://localhost:3001. To use it on a phone, the page needs HTTPS for the microphone, for example through `ngrok http 3001`.

## Frontend

The frontend is a Next.js app in `web/`. It has two ways to talk:

- **Live**: a hands-free conversation. The browser keeps listening, notices when you stop talking, and sends what you said. While the assistant answers you can just talk to interrupt it.
- **Tap to talk**: tap to start, tap again to send.

And two assistants: **Chat** (the default) talks about anything, like a general assistant; **Hospital** is the reception guide with a French summary for staff. The API takes the same choice as `{"mode": "general" | "hospital"}` on `POST /v1/sessions`, and `DEFAULT_MODE` sets the default.

 It proxies `/v1/*` to the API (`API_URL`, default `http://localhost:8080`).

```bash
cd web
npm install
npm run dev                 # http://localhost:3001
```

## Translate and Learn

The frontend has three sections: **Talk** (the assistant), **Translate** and **Learn**.

**Translate** is a face-to-face interpreter for someone who speaks only Ewe and someone who speaks English or French. Choose English or Français at the top; Ewe is always the other side (Ewe ⇄ English, Ewe ⇄ French; English ⇄ French is not offered). Each person has a side with a microphone and a text box. With **Start live interpreting** the app listens hands-free: whoever's side is highlighted speaks, hears nothing back until the translation is spoken to the other person, and then the turn passes to them. Tap a side to give it the turn.

**Learn** looks up a word or short phrase typed in English, French or Ewe, and shows it in Ewe with a simple pronunciation, an example sentence and a short tip. The ▶ buttons play the Ewe word and the example.

How it works: Muse Spark translates and writes the lessons. Ewe speech is heard by the same MMS models as the assistant; English and French speech is heard by `facebook/mms-1b-all` with its English or French adapter (it is shared with the Ewe recognizer when it is in `ASR_MODELS`; `MULTILINGUAL_ASR_MODEL` picks another). Ewe is spoken by EchoVoiice (or MMS-TTS); English and French by the browser's own voices.

## API

| Endpoint | What it does |
| --- | --- |
| `POST /v1/sessions` | Starts a conversation; returns a session id and the spoken Ewe greeting |
| `POST /v1/converse` | Form fields `session_id` and `audio` (WAV). Returns the transcript, the Ewe reply, the reply audio (base64 WAV) and the staff summary |
| `POST /v1/translate` | JSON `text`, `source`, `target` (one of them `ewe`, the other `eng` or `fra`). Returns the translation, with Ewe audio when translating into Ewe |
| `POST /v1/translate/audio` | Form fields `source`, `target` and `audio` (WAV). Same, starting from speech |
| `POST /v1/learn` | JSON `text`, `source` (`eng`, `fra` or `ewe`) and, for Ewe words, `other` (`eng` or `fra`). Returns the Ewe, its meaning, pronunciation, an example and audio |
| `POST /v1/converse/text` | JSON `{"session_id", "text"}`; the same loop with typed Ewe |
| `GET /v1/sessions/{id}/summary` | French summary for staff, urgent flag, department |
| `GET /health` | Configuration status |

Interactive docs are at `/docs`.

## Tests

```bash
pytest
```

The tests replace the models and the APIs with fakes, so they run offline in under a second.
