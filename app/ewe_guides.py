"""Ewe guidance shared by every Muse Spark prompt, read from app/prompts/.

- ewe_style.md: how the Ewe we write should sound.
- ewe_reference.md: everyday words, grammar and speech recognition quirks, so Muse Spark reads Ewe
  correctly. On our benchmark it took Ewe -> English from 61/80 to 78/80 on everyday sentences.
- data/ewe_lexicon.csv: the team's dialect and spelling variants, each with its standard Ewe.

The files are read on every request, so edits apply without a restart. HTML comments are left out.
"""
import csv
import re
import unicodedata
from typing import Dict, List, Optional

from .config import BASE_DIR

PROMPTS_DIR = BASE_DIR / "prompts"
LEXICON_FILE = BASE_DIR / "data" / "ewe_lexicon.csv"

READING_HINT = (
    "When the Ewe comes from speech recognition, first silently repair spacing, letters and tones "
    "with the reference below, then work with it. When unsure, choose the plain everyday meaning that "
    "fits the whole message; never invent events."
)


def prompt_file(name: str) -> str:
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def ewe_style_guide() -> str:
    return prompt_file("ewe_style.md")


def load_lexicon() -> List[Dict[str, str]]:
    """Rows of the lexicon, leaving out those marked Status=check (waiting for a native speaker)."""
    if not LEXICON_FILE.exists():
        return []
    with LEXICON_FILE.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if (r.get("Status") or "ok").strip().lower() != "check" and r.get("Standard_Ewe")]


# Keyboard and recognizer fallbacks, so "Mede kuku!", "medekuku" and "Meɖe kuku" share one key
_FOLD = str.maketrans("ɖʋƒŋɔɛɣ", "dvfnoeg")


def _key(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[\W_]+", "", text.translate(_FOLD))


def normalize_whole(text: str) -> Optional[str]:
    """The standard Ewe for a message that is, as a whole, a known variant; None otherwise.

    Only whole messages are replaced: swapping single words would break ordinary Ewe
    (a row like "Wu" -> "Ʋu" would ruin "dɔ le wuyem", I'm hungry).
    """
    key = _key(text)
    if not key:
        return None
    for row in load_lexicon():
        if row.get("Variation_Type", "").strip() == "Phrase Extension":
            continue  # already correct Ewe ("Akpe na wò"), only longer: keep what was said
        if key in (_key(row["User_Input_Variation"]), _key(row["Standard_Ewe"])):
            return row["Standard_Ewe"].strip()
    return None


def lexicon_section() -> str:
    variants: Dict[tuple, List[str]] = {}
    for row in load_lexicon():
        standard = (row["Standard_Ewe"].strip(), (row.get("English_Translation") or "").strip())
        variants.setdefault(standard, []).append(f'"{row["User_Input_Variation"].strip()}"')
    if not variants:
        return ""
    lines = [f"{' / '.join(v)} = {s} ({meaning})" for (s, meaning), v in variants.items()]
    return "### Dialect and spelling variants (from the team)\n" + "\n".join(lines)


def ewe_reference() -> str:
    """The reference sheet and the team's variants, with the instruction on how to use them."""
    parts = [READING_HINT, prompt_file("ewe_reference.md"), lexicon_section()]
    return "\n\n".join(p for p in parts if p)
