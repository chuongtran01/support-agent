import re

from app.config import settings
from app.retrieval.rerank import get_reranker
from app.retrieval.search import SearchResult, search
from app.tools.context import ToolContext
from app.tools.results import PassageView, SearchKnowledgeResult

MAX_QUERY = 500
TOP_K = 5

_CLOSING_TAG = re.compile(r"</(?=\s*source)", re.IGNORECASE)


def _as_data(content: str) -> str:
    """Mark a passage as source material. The closing tag is defused so a document cannot
    end the block early and put text outside it."""
    return f"<source>\n{_CLOSING_TAG.sub('<\\\\/', content)}\n</source>"


def _passage(found: SearchResult) -> PassageView:
    return PassageView(
        document_id=found.document_id,
        title=found.title,
        status=found.status,
        updated_at=found.updated_at.isoformat(),
        heading_path=found.heading_path,
        content=_as_data(found.content),
    )


def _low_confidence(found: list[SearchResult]) -> bool:
    """True when no passage is close to the question. This is a hint for the model, not a
    gate: the scores of questions with and without an answer overlap too much to cut on."""
    scores = [r.vector_score for r in found if r.vector_score is not None]
    return not scores or max(scores) < settings.low_confidence_score


async def search_knowledge(ctx: ToolContext, query: str) -> SearchKnowledgeResult:
    """Find the passages that best match a question. Read-only, and the same for every customer."""
    if ctx.embedder is None:
        raise RuntimeError("search_knowledge needs an embedder in the tool context")
    query = query.strip()
    if not query:
        return SearchKnowledgeResult(
            status="no_results", low_confidence=True, message="Empty query."
        )
    reranker = get_reranker()
    if reranker:
        found = await search(
            ctx.session, query, "rerank", TOP_K, embedder=ctx.embedder, reranker=reranker
        )
    else:
        found = await search(ctx.session, query, mode="hybrid", limit=TOP_K, embedder=ctx.embedder)
    if not found:
        return SearchKnowledgeResult(
            status="no_results", low_confidence=True, message="No help articles matched."
        )
    return SearchKnowledgeResult(
        status="ok",
        low_confidence=_low_confidence(found),
        passages=[_passage(r) for r in found],
    )
