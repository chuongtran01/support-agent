"""Split a Markdown document body into heading-scoped chunks.

Each chunk is one section (or one piece of a long section) plus its heading
path, for example ``Returns Policy > Special categories > Tents and shelters``.
The heading path is repeated on every piece so a chunk makes sense on its own.
"""

import re
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 1200

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    heading_path: str
    content: str

    @property
    def embedding_text(self) -> str:
        """Text sent to the embedding model; the full-text index uses the same parts."""
        return f"{self.heading_path}\n\n{self.content}"


def chunk_markdown(body: str, title: str = "", max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Chunk a Markdown body by heading, splitting long sections on paragraph boundaries.

    ``title`` is the root of the heading path when the body has no H1 or has text
    before its first heading. A single paragraph longer than ``max_chars`` is split
    on lines, then sentences; a table is never split.
    """
    chunks = []
    for path, text in _sections(body, title):
        for piece in _pack(_blocks(text), max_chars):
            chunks.append(Chunk(path, piece))
    return chunks


def _sections(body: str, title: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []
    in_fence = False

    def path() -> str:
        names = [name for _, name in stack]
        if title and (not stack or stack[0][0] > 1):
            names.insert(0, title)
        return " > ".join(names)

    def flush() -> None:
        text = "\n".join(lines).strip()
        if text:
            sections.append((path(), text))
        lines.clear()

    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2)))
        else:
            lines.append(line)
    flush()
    return sections


def _blocks(text: str) -> list[str]:
    return [block.strip("\n") for block in re.split(r"\n\s*\n", text) if block.strip()]


def _pack(blocks: list[str], max_chars: int) -> list[str]:
    pieces = [piece for block in blocks for piece in _split_block(block, max_chars)]
    return _greedy(pieces, "\n\n", max_chars)


def _split_block(block: str, max_chars: int) -> list[str]:
    if len(block) <= max_chars or block.lstrip().startswith("|"):
        return [block]
    lines = block.split("\n")
    if len(lines) > 1:
        return _greedy(lines, "\n", max_chars)
    return _greedy(_SENTENCE_END.split(block), " ", max_chars)


def _greedy(parts: list[str], sep: str, max_chars: int) -> list[str]:
    out: list[str] = []
    current = ""
    for part in parts:
        if current and len(current) + len(sep) + len(part) > max_chars:
            out.append(current)
            current = part
        else:
            current = f"{current}{sep}{part}" if current else part
    if current:
        out.append(current)
    return out
