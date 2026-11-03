# EchoVoiice Assistant

A voice app for people who speak **Ewe**. It has three parts:

- **Talk**: have a spoken conversation in Ewe with an assistant that can talk about anything. A hospital mode turns it into a reception guide that also writes a French summary for staff.
- **Translate**: a face-to-face interpreter between someone who speaks only Ewe and someone who speaks English or French.
- **Learn**: type a word in English, French or Ewe and see it in Ewe, hear how it sounds, and use it in a sentence.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026), theme **Build for Your World**.

## How it works

```
Someone speaks Ewe
  -> Meta MMS, fine-tuned on everyday Ewe          speech to Ewe text
  -> Muse Spark (Meta Model API)                   understands, answers or translates, in Ewe
  -> EchoVoiice TTS (Meta MMS-TTS as fallback)     Ewe text to speech
They hear the reply in Ewe
```

Muse Spark, through the Meta Model API, does all the language work: conversation, translation, word lessons and, on the Translate page, deciding which language was spoken. It reads and writes Ewe directly, with an everyday Ewe reference sheet in its prompt (see below), so there is no separate translation model. English and French speech is transcribed by Muse Voice Transcribe on the same API. Only Ewe speech recognition and the fallback voice run locally.

### Hearing everyday Ewe

Ewe speech is transcribed by [`waxal-benchmarking/mms-300m-waxal-ewe`](https://huggingface.co/waxal-benchmarking/mms-300m-waxal-ewe), Meta MMS fine-tuned on everyday conversational Ewe from Google's WAXAL corpus (model licence CC BY-NC 4.0).

We chose it by measuring. On 30 real conversational recordings from the WAXAL Ewe test set (about 10 minutes of speech), the share of words each setup got wrong was:

| Setup | Word error rate | Character error rate |
| --- | --- | --- |
| **`mms-300m-waxal-ewe` alone (used)** | **33.6%** | **13.5%** |
| Muse Spark combining it with `facebook/mms-1b-all` (the previous setup) | 39.8% | 16.6% |
| Same, but told to trust the everyday model first | 32.9% | 13.4% |
| `facebook/mms-1b-all` alone (trained mostly on Bible readings) | 48.3% | 16.9% |
| Muse Spark 1.2 hearing the audio, with the MMS versions as hints | 36.7% | 14.9% |
| Muse Spark 1.2 hearing the audio alone | 78.8% | 37.6% |

Adding `facebook/mms-1b-all` barely helps at best, while costing about 1.5 seconds per turn and 4 GB of memory, so the everyday model runs alone. Muse Spark still reads every transcript knowing it may contain recognition errors, and works out what the person meant. To add models, list them in `ASR_MODELS`.

English and French speech (Translate page) is transcribed by Muse Voice Transcribe, and English and French replies are spoken by the browser's own voices.

### Understanding everyday Ewe

Once Ewe is written down, the hard part is reading it right. Muse Spark on its own mistranslated even carefully typed Ewe: `Dɔ le wuyem` ("I'm hungry") came out as "I'm sick", `Kɔdzi la le afikae?` ("Where is the hospital?") as "Where is the key?", and Muslims praying as thieves making noise. Turning up its reasoning effort made it 2 to 5 times slower without fixing this.

So every Muse Spark prompt that reads or writes Ewe (Talk, Translate, live language detection, Learn) includes [`app/prompts/ewe_reference.md`](app/prompts/ewe_reference.md). It is a reference sheet with about 250 everyday words, the grammar that changes meaning (negation, questions, tense), and the mistakes speech recognition makes (missing tone marks, joined words, swapped letters). Muse Spark is told to repair the transcript with it first, and to choose the plain everyday meaning when unsure.

We measured Ewe → English on 20 everyday sentences (each one as typed and as speech recognition writes it) and 11 WAXAL recordings (human and machine transcripts). Each translation was scored 2 (right), 1 (partly right) or 0 (wrong):

| Setup | Everyday sentences (of 80) | WAXAL recordings (of 44) | Time per translation |
| --- | --- | --- | --- |
| Muse Spark alone (before) | 61 | 13 | 3.1 s |
| **Muse Spark + Ewe reference (used)** | **78** | **39** | **2.4 s** |
| Meta NLLB-200 draft, corrected by Muse Spark | 80 | 40 | 2.6 s + 6.5 s for NLLB |
| Meta NLLB-200 alone | 59 | 11 | 6.5 s |

The reference sheet was written while looking at these test sentences, so the scores are on the optimistic side. Adding NLLB-200 barely helped and made every translation 6.5 s slower, so translation stays on Muse Spark alone.

**Teach it more Ewe:** when a word or phrase comes out wrong, add it under "Added by the team" at the end of `ewe_reference.md`. It is read on every request, so no restart is needed (in Docker, rebuild or mount the file). A native speaker should review the sheet.

### Dialects and spellings

[`app/data/ewe_lexicon.csv`](app/data/ewe_lexicon.csv) lists dialect forms (Aŋlɔ, inland Kpelegbe, Gen/Mina), phonetic spellings and keyboard fallbacks (`d` for `ɖ`, `v` for `ʋ`), each with its standard Ewe:

| Column | Meaning |
| --- | --- |
| `Standard_Ewe` | The standard form, e.g. `Meɖe kuku` |
| `User_Input_Variation` | What people say or type, e.g. `Mede kuku` |
| `Variation_Type` | Orthography, Phonetic Text, Dialect Swap, Contraction, Vowel Shift or Phrase Extension |
| `English_Translation`, `Dialect_Note` | What it means, and where it is said |
| `Status` | `ok` to use the row, `check` to skip it until a native speaker confirms it |

The lexicon is used in two ways, without any extra API call:

- **A whole message that is a known variant is replaced before Muse Spark sees it.** For example, "medekuku", "Mede kuku!" or "woezor" become `Meɖe kuku` or `Woezɔ`. Matching ignores case, punctuation, tone marks and `ɖ/d`, `ʋ/v`, `ƒ/f`, `ŋ/n`, `ɔ/o`, `ɛ/e` swaps. Single words inside a sentence are never swapped, because a row like `Wu → Ʋu` would break `dɔ le wuyem` ("I'm hungry"). "Phrase Extension" rows are already correct Ewe and are kept as said.
- **Every variant goes into Muse Spark's Ewe reference** as a hint, so it also recognises them inside longer sentences.

Rows that looked doubtful when the file was added are marked `check` (for example `Eƒo ã?` for "have you woken up", where "wake up" is `fɔ`). To use one of them, fix it if needed and set it to `ok`. With the lexicon in place, the everyday benchmark above scores 80 of 80.

## Talk

Two assistants:

- **Chat** (the default) talks about anything, like a general assistant: greetings, small talk, questions on any subject.
- **Hospital** is an interpreter and guide at a hospital entrance, not a medical tool. It helps patients describe what they feel, explains where departments are and what happens next, and repeats staff instructions in Ewe. It never diagnoses, never suggests medicine, and never judges how serious something is. If a patient describes an urgent sign, it tells them to alert staff right away and flags the summary as urgent. Hospital facts come only from [`app/data/hospital.json`](app/data/hospital.json), which the hospital edits. Staff see a French summary under **Staff**.

Two ways to talk:

- **Live**: a hands-free conversation. The browser keeps listening, notices when you stop talking, and sends what you said. While the assistant answers you can just talk to interrupt it. **Pause before reply** sets how long it waits after you stop: Short (0.7 s), Normal (1.1 s, the default) or Long (1.6 s, more time to think mid-sentence without being cut off).
- **Tap to talk**: tap to start, tap again to send.

You can also type in Ewe.

## Speed

Replies are spoken sentence by sentence: as soon as Muse Spark has written the first sentence it is voiced and played, while the rest is still being written and voiced. Measured on this setup:

| | First words heard after the request |
| --- | --- |
| Talk, typed or spoken Ewe | about 1.5 to 2 s (was 3 to 5 s) |
| Translate, spoken English or French into Ewe, language detected automatically | about 3.6 to 4 s (was 4.5 to 5 s, often more) |

Other things that keep it quick: one kept-open connection to the Meta Model API (a new secure connection per call cost about 0.6 s), `META_REASONING_EFFORT=minimal`, a single everyday-Ewe speech model, and a Muse Spark call that takes longer than `LLM_TIMEOUT` is retried instead of waited on. When EchoVoiice keeps failing, the app waits longer each time before trying it again (up to an hour).

## Translate

Choose English or Français at the top; Ewe is always the other side. The pairs are Ewe ⇄ English and Ewe ⇄ French; English ⇄ French is not offered.

With **Start live interpreting** the app listens hands-free (with the same **Pause before reply** setting as Talk) and either person just talks: it works out whether they spoke Ewe or the other language and speaks the translation to the other person. To do that, the Ewe recognizer and Muse Voice Transcribe both hear each recording, and Muse Spark decides which version is real speech, then translates it. If it guesses wrong, set **Who speaks** to one language. The microphone stays off while a translation plays, so the app does not translate its own voice.

Each translation can be fixed:

- **Say again** removes it so the person can repeat themselves (in live mode, the app then listens for that language).
- **Wrong language** translates the same recording again as the other language, without speaking again.
- **Edit** puts the text in the box to correct it, then Go translates it again.

Without live mode, tap the microphone of the language you speak, and tap again to stop. Each language also has a text box.

## Learn

Type a word or short phrase in English, French or Ewe (for an Ewe word, choose whether to see it in English or French). The lesson shows the Ewe in large type, a simple pronunciation, an example sentence with its translation, and a short tip. The ▶ buttons play the Ewe word and the example; the English or French plays in the browser's voice. Recent words stay on the page.

## What is new in this hackathon

Everything in this repository was written during the hackathon:

- the conversation loop and session memory (`app/conversation.py`) and the prompts (`app/prompts/`)
- Ewe speech recognition with Meta MMS (`app/asr.py`), chosen by measuring on real everyday Ewe, and English and French with Muse Voice Transcribe (`app/transcribe.py`)
- translation, spoken-language detection and word lessons with Muse Spark (`app/language.py`)
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

The first start downloads the Meta speech models (about 1.5 GB) and takes a few minutes. They are kept in a Docker volume, so later starts are quick. Give Docker at least 4 GB of memory.

On Apple Silicon Macs, EchoVoiice may crash while generating speech (a PyTorch segmentation fault on ARM). The app then uses the MMS-TTS voice and skips EchoVoiice for `ECHOVOIICE_RETRY_SECONDS`; on x86 servers EchoVoiice is used normally.

## Run it without Docker

You need Python 3.10 or newer and Node.js 20 or newer. The Meta speech models download from Hugging Face on first use (about 1.5 GB).

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
| `LLM_TIMEOUT` | `20` | Seconds to wait for one Muse Spark answer before trying once more |
| `ASR_MODELS` | `waxal-benchmarking/mms-300m-waxal-ewe` | Ewe speech models, comma-separated; all run on every recording |
| `ASR_LANGUAGE` | `ewe` | Language adapter for multilingual MMS models such as `facebook/mms-1b-all` |
| `VOICE_TRANSCRIBE_MODEL` | `muse-voice-transcribe-1.0` | Transcribes English and French on the Translate page |
| `ECHOVOIICE_URL` | `http://localhost:8000` | EchoVoiice TTS API |
| `ECHOVOIICE_API_KEY` | | EchoVoiice key; or leave empty and set the two account variables below |
| `ECHOVOIICE_ACCOUNT_EMAIL`, `ECHOVOIICE_ACCOUNT_PASSWORD` | | Account the assistant signs in with (and creates) to get its own key |
| `TTS_SPEED` | `1.1` | EchoVoiice speaking speed, 0.5 to 2.0 |
| `USE_TTS_FALLBACK` | `true` | Speak with Meta MMS-TTS when EchoVoiice fails |
| `TTS_FALLBACK_MODEL` | `facebook/mms-tts-ewe` | The fallback voice |
| `ECHOVOIICE_RETRY_SECONDS` | `300` | After EchoVoiice fails, use the fallback straight away for this long; doubled after each failure in a row, up to an hour |
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
| `POST /v1/translate/audio` | Form fields `other` (`eng` or `fra`), `audio` (WAV) and optional `source` (`auto`, the default, or `ewe`/`eng`/`fra`). With `auto`, works out which language was spoken. Returns the detected `source` and `target` with the translation |
| `POST /v1/converse/stream` | Form fields `session_id` and `audio` (WAV) or `text`. Streams the reply sentence by sentence as NDJSON (see below) |
| `POST /v1/translate/stream` | Form fields `other`, `source` (`auto` needs `audio`) and `audio` or `text`. Streams the translation sentence by sentence as NDJSON |
| `POST /v1/learn` | JSON `text`, `source` (`eng`, `fra` or `ewe`) and, for Ewe words, `other` (`eng` or `fra`). Returns the Ewe, its meaning, pronunciation, an example and audio |
| `GET /health` | Configuration status |

The server logs how long each step takes: `Heard ... (asr=...)`, `Timing: llm=... tts=...`, and for streamed replies `Timing: first sentence ready after ...`.

The streaming endpoints send one JSON event per line: `{"type": "heard", "text", "alternatives", ...}` with what was said, then `{"type": "sentence", "text", "audio_wav_base64"}` for each sentence in order (Ewe comes with audio; English and French are spoken by the browser), then `{"type": "done", "text", ...}` with the whole reply. If something fails after the stream has started, the last line is `{"type": "error", "detail"}`.

## Tests

```bash
pytest
```

The tests replace the models and the APIs with fakes, so they run offline in under a second.
