"""Translation and word lessons between Ewe and English or French, with Muse Spark.

Translation always has Ewe on one side: Ewe <-> English and Ewe <-> French.
"""
import json
import re
from typing import Dict, List, Optional

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


async def translate(
    llm: ChatModel, text: str, source: str, target: str, alternatives: Optional[List[str]] = None
) -> str:
    check_pair(source, target)
    other = target if source == "ewe" else source
    style = EWE_STYLE if target == "ewe" else f"Write natural, everyday {LANGUAGES[target]}."
    system = TRANSLATE_PROMPT.format(
        other=LANGUAGES[other], source=LANGUAGES[source], target=LANGUAGES[target], style=style
    )
    message = text
    if alternatives:
        versions = "\n".join(f"{i}. {t}" for i, t in enumerate([text, *alternatives], 1))
        message = (
            "Speech recognition heard the same words in several ways. Combine them to work out "
            f"what was said, then translate it:\n{versions}"
        )
    reply = await llm.chat([{"role": "system", "content": system}, {"role": "user", "content": message}])
    return reply.strip().strip('"“”').strip()


async def lesson(llm: ChatModel, text: str, source: str, other: str) -> Dict[str, str]:
    if source not in LANGUAGES or other not in ("eng", "fra"):
        raise ValueError("Learn words from or into Ewe, with English or French.")
    system = LEARN_PROMPT.format(other=LANGUAGES[other], source=LANGUAGES[source], style=EWE_STYLE)
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
