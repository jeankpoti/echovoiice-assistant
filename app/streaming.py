"""Speak replies sentence by sentence while Muse Spark is still writing them.

Muse Spark's text arrives in small pieces. As soon as a sentence is complete it is
voiced and sent to the browser, which starts playing it while later sentences are
still being written and voiced.
"""
import asyncio
import re
from typing import AsyncIterator, Awaitable, Callable, Optional

# The end of a sentence: punctuation (and any closing quote or bracket) before a space, or a line break
BOUNDARY = re.compile(r"[.!?…]+[\"”»)\]]*(?=\s)|\n")
FIRST_MIN_CHARS = 12  # start speaking early, but not on a lone "Ee!"
NEXT_MIN_CHARS = 40  # later pieces are joined into longer ones, which sound more natural


def _clean(sentence: str) -> str:
    return sentence.strip().strip('"“”').strip()


async def sentences(deltas: AsyncIterator[str]) -> AsyncIterator[str]:
    """Group streamed text into sentences."""
    buffer = ""
    min_chars = FIRST_MIN_CHARS
    async for delta in deltas:
        buffer += delta
        while True:
            cut = next((m.end() for m in BOUNDARY.finditer(buffer) if m.end() >= min_chars), None)
            if cut is None:
                break
            sentence, buffer = _clean(buffer[:cut]), buffer[cut:]
            if sentence:
                yield sentence
                min_chars = NEXT_MIN_CHARS
    if _clean(buffer):
        yield _clean(buffer)


async def spoken_sentences(
    deltas: AsyncIterator[str], voice: Optional[Callable[[str], Awaitable[dict]]] = None
) -> AsyncIterator[dict]:
    """Yield {"text": sentence, **voice(sentence)} in order. Voicing runs while the text keeps
    streaming, one sentence at a time so they do not compete for the CPU."""
    queue: asyncio.Queue = asyncio.Queue()
    lock = asyncio.Lock()  # first come, first served, so sentences are voiced in order

    async def voiced(text: str) -> dict:
        if voice is None:
            return {}
        async with lock:
            return await voice(text)

    async def produce() -> None:
        try:
            async for sentence in sentences(deltas):
                queue.put_nowait((sentence, asyncio.create_task(voiced(sentence))))
        except Exception as e:  # handed to the reader, which raises it
            queue.put_nowait(e)
            return
        queue.put_nowait(None)

    producer = asyncio.create_task(produce())
    try:
        while (item := await queue.get()) is not None:
            if isinstance(item, Exception):
                raise item
            text, task = item
            yield {"text": text, **(await task)}
    finally:
        producer.cancel()
