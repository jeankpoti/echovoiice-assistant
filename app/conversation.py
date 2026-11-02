"""The conversation loop: Ewe speech in, Muse Spark in the middle, Ewe speech out."""
import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from .config import BASE_DIR, settings
from .llm import ChatModel

PROMPTS_DIR = BASE_DIR / "prompts"

# "general" chats about anything; "hospital" is the reception guide with a staff summary.
MODES = ("general", "hospital")

# Ask an Ewe speaker to check this before the demo.
GREETING_EWE = "Woezɔ! Nye ŋkɔe nye EchoVoiice. Aleke mate ŋu akpe ɖe ŋuwò egbe?"
GREETING_FR = "Bienvenue ! Je m'appelle EchoVoiice. Comment puis-je vous aider aujourd'hui ?"

REPLY_IN_EWE = "Reply in Ewe (Eʋegbe), the language the person speaks. Their messages are in Ewe."
REPLY_IN_FRENCH = (
    "Reply in simple French. The person's messages were translated from Ewe to French by a machine, "
    "and your reply will be translated back to Ewe, so use plain words and short sentences."
)


@dataclass
class Turn:
    user_text: str
    reply_ewe: str
    reply_fr: Optional[str]
    staff_summary_fr: str
    urgent: bool
    department: Optional[str]
    user_text_fr: Optional[str] = None


@dataclass
class Session:
    id: str
    mode: str = "general"
    history: List[Dict[str, str]] = field(default_factory=list)
    staff_summary_fr: str = ""
    urgent: bool = False
    department: Optional[str] = None
    turns: List[Turn] = field(default_factory=list)


def ewe_style_guide() -> str:
    """Shared guidance (and examples you add) for everyday, spoken Ewe; HTML comments are left out."""
    text = (PROMPTS_DIR / "ewe_style.md").read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def build_system_prompt(use_translation: bool, mode: str = "general") -> str:
    template = (PROMPTS_DIR / f"{mode}.md").read_text(encoding="utf-8")
    language = REPLY_IN_FRENCH if use_translation else REPLY_IN_EWE
    if mode == "hospital":
        facility = settings.facility_file.read_text(encoding="utf-8")
        prompt = template.format(reply_language_instruction=language, facility=facility)
    else:
        prompt = template.format(reply_language_instruction=language, today=date.today().strftime("%A %d %B %Y"))
    if use_translation:
        return prompt
    return f"{prompt}\n\n{ewe_style_guide()}"


def format_heard(heard: List[str]) -> str:
    """One version goes to the model as is; several are labelled so it can compare them."""
    if len(heard) == 1:
        return heard[0]
    lines = [
        "[Speech recognition heard the person in different ways. Each version may contain errors; "
        "work out what they most likely said.]"
    ]
    lines += [f"Version {chr(65 + i)}: {h}" for i, h in enumerate(heard)]
    return "\n".join(lines)


def parse_reply(raw: str) -> Dict:
    """Pull the JSON object out of the model's answer; fall back to plain text."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and isinstance(data.get("reply"), str):
                return data
        except json.JSONDecodeError:
            pass
    return {"reply": raw.strip(), "staff_summary_fr": None, "urgent": False, "department": None}


class Assistant:
    def __init__(self, llm: ChatModel, translator=None, use_translation: bool = settings.use_translation):
        self.llm = llm
        self.translator = translator
        self.use_translation = use_translation and translator is not None
        self.sessions: Dict[str, Session] = {}

    def start_session(self, mode: Optional[str] = None) -> Session:
        mode = mode or settings.default_mode
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}. Use one of: {', '.join(MODES)}.")
        session = Session(id=uuid.uuid4().hex, mode=mode)
        self.sessions[session.id] = session
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self.sessions.get(session_id)

    async def respond(self, session: Session, user_text: str, alternatives: Optional[List[str]] = None) -> Turn:
        """user_text is what the person said; alternatives are other speech-recognition
        versions of the same audio, from models tuned for other kinds of Ewe."""
        user_text_fr = None
        heard = [user_text] + [a for a in (alternatives or []) if a != user_text]
        if self.use_translation:
            heard = [await asyncio.to_thread(self.translator.ewe_to_french, h) for h in heard]
            user_text_fr = heard[0]
        model_input = format_heard(heard)

        messages = [{"role": "system", "content": build_system_prompt(self.use_translation, session.mode)}]
        messages += session.history
        messages.append({"role": "user", "content": model_input})

        raw = await self.llm.chat(messages)
        data = parse_reply(raw)
        reply = data["reply"]

        if self.use_translation:
            reply_fr = reply
            reply_ewe = await asyncio.to_thread(self.translator.french_to_ewe, reply)
        else:
            reply_fr = None
            reply_ewe = reply

        session.history += [
            {"role": "user", "content": model_input},
            {"role": "assistant", "content": raw},
        ]
        session.history = session.history[-2 * settings.max_history_turns :]
        if data.get("staff_summary_fr"):
            session.staff_summary_fr = data["staff_summary_fr"]
        session.urgent = session.urgent or bool(data.get("urgent"))
        if data.get("department"):
            session.department = data["department"]

        turn = Turn(
            user_text=user_text,
            user_text_fr=user_text_fr,
            reply_ewe=reply_ewe,
            reply_fr=reply_fr,
            staff_summary_fr=session.staff_summary_fr,
            urgent=session.urgent,
            department=session.department,
        )
        session.turns.append(turn)
        return turn
