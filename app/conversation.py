"""The conversation loop: Ewe speech in, Muse Spark in the middle, Ewe speech out."""
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import AsyncIterator, Dict, List, Optional

from .config import settings
from .ewe_guides import PROMPTS_DIR, ewe_reference, ewe_style_guide, normalize_whole
from .llm import ChatModel

# "general" chats about anything; "hospital" is the reception guide with a staff summary.
MODES = ("general", "hospital")

# Ask an Ewe speaker to check this before the demo.
GREETING_EWE = "Woezɔ! Nye ŋkɔe nye EchoVoiice. Aleke mate ŋu akpe ɖe ŋuwò egbe?"
GREETING_FR = "Bienvenue ! Je m'appelle EchoVoiice. Comment puis-je vous aider aujourd'hui ?"

REPLY_IN_EWE = "Reply in Ewe (Eʋegbe), the language the person speaks. Their messages are in Ewe."


@dataclass
class Turn:
    user_text: str
    reply_ewe: str
    staff_summary_fr: str
    urgent: bool
    department: Optional[str]


@dataclass
class Session:
    id: str
    mode: str = "general"
    history: List[Dict[str, str]] = field(default_factory=list)
    staff_summary_fr: str = ""
    urgent: bool = False
    department: Optional[str] = None
    turns: List[Turn] = field(default_factory=list)


def build_system_prompt(mode: str = "general") -> str:
    template = (PROMPTS_DIR / f"{mode}.md").read_text(encoding="utf-8")
    if mode == "hospital":
        facility = settings.facility_file.read_text(encoding="utf-8")
        prompt = template.format(reply_language_instruction=REPLY_IN_EWE, facility=facility)
    else:
        prompt = template.format(
            reply_language_instruction=REPLY_IN_EWE, today=date.today().strftime("%A %d %B %Y")
        )
    return f"{prompt}\n\n{ewe_style_guide()}\n\n{ewe_reference()}"


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
    def __init__(self, llm: ChatModel):
        self.llm = llm
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

    def _prepare(self, session: Session, user_text: str, alternatives: Optional[List[str]]):
        # A known dialect or spelling variant, said on its own, becomes standard Ewe
        heard = [normalize_whole(user_text) or user_text] + [a for a in (alternatives or []) if a != user_text]
        model_input = format_heard(heard)
        messages = [{"role": "system", "content": build_system_prompt(session.mode)}]
        messages += session.history
        messages.append({"role": "user", "content": model_input})
        return model_input, messages

    def _record(self, session: Session, user_text: str, model_input: str, raw: str) -> Turn:
        data = parse_reply(raw)
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
            reply_ewe=data["reply"],
            staff_summary_fr=session.staff_summary_fr,
            urgent=session.urgent,
            department=session.department,
        )
        session.turns.append(turn)
        return turn

    async def respond(self, session: Session, user_text: str, alternatives: Optional[List[str]] = None) -> Turn:
        """user_text is what the person said; alternatives are other speech-recognition
        versions of the same audio, from other Ewe models (ASR_MODELS)."""
        model_input, messages = self._prepare(session, user_text, alternatives)
        raw = await self.llm.chat(messages)
        return self._record(session, user_text, model_input, raw)

    async def respond_stream(
        self, session: Session, user_text: str, alternatives: Optional[List[str]] = None
    ) -> AsyncIterator[str]:
        """Like respond, but yields the reply in pieces as it is written. The finished
        turn is then session.turns[-1]."""
        model_input, messages = self._prepare(session, user_text, alternatives)
        if session.mode == "hospital":
            # The hospital guide answers in JSON, which can only be read once complete
            raw = await self.llm.chat(messages)
            yield self._record(session, user_text, model_input, raw).reply_ewe
            return
        raw = ""
        async for delta in self.llm.stream(messages):
            raw += delta
            yield delta
        self._record(session, user_text, model_input, raw)
