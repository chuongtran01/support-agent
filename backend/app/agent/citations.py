"""Check the document ids an answer cites against what was retrieved in the turn.

The model writes ``[document_id]`` after a fact. An id it never retrieved is dropped, so a
made-up source never reaches the customer as if it were real.
"""

import json
import re

from app.agent.events import Citation

# A document id starts with a letter, so footnote-style markers such as [1] are not citations
_MARKER = re.compile(r"\[([a-z][a-z0-9]*(?:-[a-z0-9]+)*)\]")


def cited_ids(text: str) -> list[str]:
    """The distinct ids cited in an answer, in the order they first appear."""
    return list(dict.fromkeys(_MARKER.findall(text)))


def retrieved_from_result(raw: str) -> dict[str, Citation]:
    """The documents in a search_knowledge result, with the heading of the best passage of each."""
    try:
        passages = json.loads(raw)["passages"]
        return {
            p["document_id"]: Citation(p["document_id"], p["title"], p["heading_path"])
            for p in reversed(passages)
        }
    except (ValueError, KeyError, TypeError):
        return {}


def check_citations(text: str, retrieved: dict[str, Citation]) -> tuple[tuple[Citation, ...], int]:
    """The citations that match a retrieved document, and how many cited ids did not."""
    ids = cited_ids(text)
    valid = tuple(retrieved[i] for i in ids if i in retrieved)
    return valid, len(ids) - len(valid)
