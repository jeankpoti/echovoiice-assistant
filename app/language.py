"""Translation and word lessons between Ewe and English or French, with Muse Spark.

Translation always has Ewe on one side: Ewe <-> English and Ewe <-> French.
"""
import json
import re
from typing import AsyncIterator, Dict, List, Optional, Tuple

from .ewe_guides import ewe_reference, normalize_whole
from .llm import ChatModel, LLMError

LANGUAGES = {"ewe": "Ewe (Eʋegbe)", "eng": "English", "fra": "French"}

EWE_STYLE = (
    "Write Ewe the way people speak it every day in Lomé, with the Ewe letters "
    "(ɖ, ƒ, ɣ, ŋ, ɔ, ɛ, ʋ) and tone marks where they are usual. Write numbers as words."
)

TRANSLATE_PROMPT = """You are an interpreter helping two people talk face to face: one speaks Ewe (Eʋegbe), the other speaks {other}.
Translate the message from {source} into {target}.
- Translate only. Never answer the message, add comments or explain. If it is a question, translate the question.
- Keep the speaker's meaning, tone and point of view: "I" stays "I" and "you" stays "you".
- Messages usually come from speech recognition, so they may have spelling mistakes, missing punctuation or wrong words. Translate what the speaker most likely meant.
- {style}
- Reply with the translation only, as plain text."""

LEARN_PROMPT = """You are a friendly Ewe (Eʋegbe) teacher for people who speak {other}.
The learner types a word or short phrase in {source}. Teach it.
Answer ONLY with one JSON object, no other text:
{{"ewe": "<the word or phrase in Ewe>", "other": "<the word or phrase in {other}>", "pronunciation": "<how to say the Ewe, spelled simply for a {other} speaker>", "example_ewe": "<a short everyday sentence in Ewe that uses it>", "example_other": "<that sentence in {other}>", "note": "<one or two short sentences in {other}: other meanings, how it is used, or a tip>"}}
- {style}
- If it has several common meanings, use the most common one and mention the others in the note.
- If it is not a real word or you are not sure, say so honestly in the note."""


def check_pair(source: str, target: str) -> None:
    if source not in LANGUAGES or target not in LANGUAGES:
        raise ValueError(f"Languages must be among: {', '.join(LANGUAGES)}.")
    if source == target or "ewe" not in (source, target):
        raise ValueError("Translation goes between Ewe and English, or Ewe and French.")


def _translate_messages(
    text: str, source: str, target: str, alternatives: Optional[List[str]] = None
) -> List[Dict[str, str]]:
    check_pair(source, target)
    other = target if source == "ewe" else source
    style = EWE_STYLE if target == "ewe" else f"Write natural, everyday {LANGUAGES[target]}."
    system = TRANSLATE_PROMPT.format(
        other=LANGUAGES[other], source=LANGUAGES[source], target=LANGUAGES[target], style=style
    )
    system = f"{system}\n\n{ewe_reference()}"
    if source == "ewe":
        text = normalize_whole(text) or text  # a known dialect or spelling variant, said on its own
    message = text
    if alternatives:
        versions = "\n".join(f"{i}. {t}" for i, t in enumerate([text, *alternatives], 1))
        message = (
            "Speech recognition heard the same words in several ways. Combine them to work out "
            f"what was said, then translate it:\n{versions}"
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": message}]


async def translate(
    llm: ChatModel, text: str, source: str, target: str, alternatives: Optional[List[str]] = None
) -> str:
    reply = await llm.chat(_translate_messages(text, source, target, alternatives))
    return reply.strip().strip('"“”').strip()


def translate_stream(
    llm: ChatModel, text: str, source: str, target: str, alternatives: Optional[List[str]] = None
) -> AsyncIterator[str]:
    """The translation in pieces, as Muse Spark writes it. Checks the pair right away."""
    return llm.stream(_translate_messages(text, source, target, alternatives))


DETECT_PROMPT = """You are an interpreter helping two people talk face to face: one speaks Ewe (Eʋegbe), the other speaks {other}.
Someone just spoke, in Ewe or in {other}. Two speech recognizers heard the same recording:
- The Ewe recognizer only knows Ewe. On {other} speech it writes Ewe-looking nonsense.
- The {other} recognizer only knows {other}. On Ewe speech it writes {other}-looking nonsense, or nothing.
First decide which language was really spoken: the version that reads as real, meaningful speech.
Then translate what was said into the other language.
- Translate only. Never answer the message, add comments or explain.
- Keep the speaker's meaning, tone and point of view: "I" stays "I" and "you" stays "you".
- Speech recognition makes mistakes; translate what the speaker most likely meant.
- When you write Ewe: {style}
Answer in exactly this form, nothing else:
first line: ewe or {code} (the language that was spoken)
next lines: the translation, as plain text"""


async def detect_and_translate_stream(
    llm: ChatModel, ewe_heard: List[str], other_heard: str, other: str
) -> Tuple[str, AsyncIterator[str]]:
    """For hands-free interpreting: work out whether Ewe or `other` was spoken. Returns that
    language and the translation in pieces, as Muse Spark writes it."""
    if other not in ("eng", "fra"):
        raise ValueError("The other language must be English or French.")
    system = DETECT_PROMPT.format(other=LANGUAGES[other], code=other, style=EWE_STYLE)
    system = f"{system}\n\n{ewe_reference()}"
    ewe_heard = [normalize_whole(t) or t for t in ewe_heard[:1]] + ewe_heard[1:]
    ewe_lines = "\n".join(f"  {t}" for t in ewe_heard) or "  (nothing)"
    message = f"Ewe recognizer:\n{ewe_lines}\n{LANGUAGES[other]} recognizer:\n  {other_heard or '(nothing)'}"
    pieces = llm.stream([{"role": "system", "content": system}, {"role": "user", "content": message}]).__aiter__()

    head = ""
    while "\n" not in head:
        try:
            head += await pieces.__anext__()
        except StopAsyncIteration:
            break
    first, _, rest = head.partition("\n")
    word = first.strip().strip("*:`\"'. ").lower()
    names = {"ewe": "ewe", "eʋegbe": "ewe", other: other, LANGUAGES[other].lower(): other}
    spoken = names.get(word)
    if spoken is None:
        raise LLMError("Could not read the translation from the model. Please try again.")

    async def translation() -> AsyncIterator[str]:
        if rest:
            yield rest
        async for piece in pieces:
            yield piece

    return spoken, translation()


async def detect_and_translate(
    llm: ChatModel, ewe_heard: List[str], other_heard: str, other: str
) -> Dict[str, str]:
    """detect_and_translate_stream, collected into one answer."""
    spoken, pieces = await detect_and_translate_stream(llm, ewe_heard, other_heard, other)
    translation = "".join([p async for p in pieces]).strip()
    if not translation:
        raise LLMError("Could not read the translation from the model. Please try again.")
    return {
        "source": spoken,
        "target": other if spoken == "ewe" else "ewe",
        "text": ewe_heard[0] if spoken == "ewe" and ewe_heard else other_heard,
        "translation": translation.strip('"“”').strip(),
    }


async def lesson(llm: ChatModel, text: str, source: str, other: str) -> Dict[str, str]:
    if source not in LANGUAGES or other not in ("eng", "fra"):
        raise ValueError("Learn words from or into Ewe, with English or French.")
    system = LEARN_PROMPT.format(other=LANGUAGES[other], source=LANGUAGES[source], style=EWE_STYLE)
    system = f"{system}\n\n{ewe_reference()}"
    raw = await llm.chat([{"role": "system", "content": system}, {"role": "user", "content": text}])
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not data.get("ewe"):
        raise LLMError("Could not read the lesson from the model. Please try again.")
    fields = ("ewe", "other", "pronunciation", "example_ewe", "example_other", "note")
    return {key: str(data.get(key) or "").strip() for key in fields}
