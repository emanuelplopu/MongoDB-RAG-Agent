"""Shared pieces of the search pipelines: the join to ``documents`` and result clean-up.

Three findings of the validation of a law firm's index (``docs/27-POST_INGESTION_VALIDATION.md``):

* every search pipeline joined ``documents`` with a plain ``$lookup``, which loads the whole
  parent document, megabytes of ``content`` included, for each of 20-100 hits, although
  only the title, the source path and the content hash are used;
* 8,600 stored reader placeholders (``[Image: logo.gif - OCR extracted minimal text]``)
  were embedded and came back for generic queries; the pipeline no longer stores them
  (:mod:`src.ingestion.placeholders`) and ``quellexctl ingest purge-placeholders`` removes
  the stored ones, but until that purge ran they must not reach a result list;
* copies of one file (the attachment next to its e-mail, the same letter in two matters)
  took several of the ten places of a result list.
"""

from __future__ import annotations

from typing import Any, Collection, Dict, List, Mapping, Sequence

from src.ingestion.placeholders import placeholder_kind

__all__ = [
    "DOCUMENT_JOIN_FIELDS",
    "ORDINARY_FETCH_FACTOR",
    "document_lookup_stage",
    "drop_placeholder_results",
    "fused_order",
    "is_placeholder_result",
]

#: Fields of the parent document the search results use (never ``content``)
DOCUMENT_JOIN_FIELDS: Dict[str, int] = {"title": 1, "source": 1, "content_hash": 1}

#: Candidates fetched per index for an ordinary hybrid query, as a multiple of the requested
#: results: copies collapse and placeholders are dropped after the fusion
ORDINARY_FETCH_FACTOR = 3


def document_lookup_stage(documents_collection: str, alias: str = "doc_info") -> Dict[str, Any]:
    """``$lookup`` stage joining a chunk's parent document without loading its content.

    Args:
        documents_collection: Name of the documents collection.
        alias: Name of the joined array field (the pipelines ``$unwind`` it).

    Returns:
        The aggregation stage.
    """
    return {
        "$lookup": {
            "from": documents_collection,
            "localField": "document_id",
            "foreignField": "_id",
            "pipeline": [{"$project": dict(DOCUMENT_JOIN_FIELDS)}],
            "as": alias,
        }
    }


def fused_order(scores: Mapping[str, float], text_ids: Collection[str]) -> List[str]:
    """Chunk ids by fused score, best first; on equal scores a text hit precedes a vector-only hit.

    Reciprocal rank fusion gives rank ``i`` of the vector stage and rank ``i`` of the text
    stage the same score. The fusions listed the vector stage first, so every such tie went
    to the vector hit: a document that only the text stage found at rank 1 came second
    behind an unrelated vector hit. On 210 benchmark queries of a law firm's index the right
    document was first for 21 % of exact-sentence queries, 12 % of keyword queries and 28 %
    of written questions; with this order 59 %, 50 % and 51 %, at the same top-ten rate
    (``docs/27-POST_INGESTION_VALIDATION.md``, section 7). Equal weights are kept, so both
    stages still share the result window.

    Args:
        scores: Fused score per chunk id.
        text_ids: Chunk ids the text stage returned.

    Returns:
        The chunk ids, best first; otherwise equal entries keep their order in ``scores``.
    """
    return sorted(scores, key=lambda chunk_id: (scores[chunk_id], chunk_id in text_ids), reverse=True)


def is_placeholder_result(result: Dict[str, Any]) -> bool:
    """Whether a search result's whole text is a reader placeholder, not document content."""
    return placeholder_kind(result.get("content")) is not None


def drop_placeholder_results(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The results without stored reader placeholders, order kept."""
    return [result for result in results if not is_placeholder_result(result)]
