"""Ordinary MongoDB indexes the ingestion pipeline and the document endpoints query by.

Until 2026-11-11 these indexes were created only by ``POST /system/database/create``. A
database that was restored, or created by the first ingestion run, had none: after one
law firm's ingestion ``documents`` (365,000 rows, 4.3 GB) carried only ``_id``. Every
stored file then scanned the collection once to delete its previous version
(``documents.source``), every identifier query scanned it for file names, and the
failure and statistics pages scanned ``ingestion_stats`` (493,000 rows) and
``failed_documents`` (``docs/27-POST_INGESTION_VALIDATION.md``).

The search indexes (vector and text) are a different matter: ``src/setup_indexes.py`` and
``backend/routers/indexes.py``.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = ["INGESTION_INDEXES", "ensure_ingestion_indexes", "ensure_ingestion_indexes_sync", "index_specs"]

IndexKeys = Sequence[Tuple[str, int]]

#: ``collection role -> [(index name, keys)]``. The roles ``documents`` and ``chunks`` map to
#: the profile's collection names; the other two collections have fixed names.
INGESTION_INDEXES: Dict[str, List[Tuple[str, IndexKeys]]] = {
    "documents": [
        ("source_1", [("source", 1)]),  # previous version of a file, incremental skip, viewer lookups
        ("content_hash_1", [("content_hash", 1)]),  # duplicate check
        ("metadata.file_path_1", [("metadata.file_path", 1)]),  # reconciliation with the share
        ("title_1", [("title", 1)]),  # file-name search of identifier queries
        ("created_at_-1", [("created_at", -1)]),  # document list
    ],
    "chunks": [
        ("document_id_1", [("document_id", 1)]),  # chunks of a document (viewer, deletes, backlog)
    ],
    "ingestion_stats": [
        ("job_id_1_completed_at_1", [("job_id", 1), ("completed_at", 1)]),  # ETA, job counters
        ("job_id_1_error_type_1", [("job_id", 1), ("error_type", 1)]),  # failure counts of a job
        ("file_path_1", [("file_path", 1)]),  # latest record of a file
        ("started_at_-1", [("started_at", -1)]),  # recent files
    ],
    "failed_documents": [
        ("job_id_1_resolved_1", [("job_id", 1), ("resolved", 1)]),  # files a resumed job skips
        ("file_path_1_error_type_1", [("file_path", 1), ("error_type", 1)]),  # resolve / replace a record
        ("resolved_1_error_type_1_failed_at_-1", [("resolved", 1), ("error_type", 1), ("failed_at", -1)]),  # failed-documents page
    ],
}


def index_specs(documents_collection: str = "documents", chunks_collection: str = "chunks") -> List[Tuple[str, str, IndexKeys]]:
    """``(collection name, index name, keys)`` for a profile's collection names."""
    names = {"documents": documents_collection, "chunks": chunks_collection}
    return [
        (names.get(role, role), index_name, keys)
        for role, indexes in INGESTION_INDEXES.items()
        for index_name, keys in indexes
    ]


async def ensure_ingestion_indexes(db: Any, documents_collection: str = "documents", chunks_collection: str = "chunks") -> List[str]:
    """Create the missing indexes (idempotent; an existing index costs one round trip).

    A failure is logged and does not stop the caller: the indexes are an optimisation, a
    run without them is slow but correct. The first build on a large ``documents``
    collection takes a few minutes.

    Args:
        db: The profile's database (PyMongo Async or Motor).
        documents_collection: Name of the documents collection.
        chunks_collection: Name of the chunks collection.

    Returns:
        ``collection.index`` of every index that is in place after the call.
    """
    ensured: List[str] = []
    for collection, index_name, keys in index_specs(documents_collection, chunks_collection):
        try:
            result = db[collection].create_index(list(keys), name=index_name)
            if inspect.isawaitable(result):
                await result
            ensured.append(f"{collection}.{index_name}")
        except Exception as exc:  # noqa: BLE001 - never fail a run over an index
            logger.warning(f"Could not create index {index_name} on {collection}: {exc}")
    return ensured


def ensure_ingestion_indexes_sync(db: Any, documents_collection: str = "documents", chunks_collection: str = "chunks") -> List[str]:
    """Synchronous twin of :func:`ensure_ingestion_indexes` for a ``pymongo.MongoClient`` database."""
    ensured: List[str] = []
    for collection, index_name, keys in index_specs(documents_collection, chunks_collection):
        try:
            db[collection].create_index(list(keys), name=index_name)
            ensured.append(f"{collection}.{index_name}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not create index {index_name} on {collection}: {exc}")
    return ensured
