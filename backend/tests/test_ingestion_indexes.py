"""The ordinary indexes of the ingestion collections exist after a run starts.

After one law firm's ingestion ``documents`` (365,000 rows) had only the ``_id`` index, so
every stored file scanned it to find its previous version (``docs/27-POST_INGESTION_VALIDATION.md``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.ingestion import indexes


class _AsyncCollection:
    def __init__(self, name: str, calls: List[Tuple[str, str, list]], fail: bool = False) -> None:
        self.name, self.calls, self.fail = name, calls, fail

    async def create_index(self, keys: list, name: str) -> str:
        if self.fail:
            raise RuntimeError("An equivalent index already exists with a different name")
        self.calls.append((self.name, name, keys))
        return name


class _SyncCollection(_AsyncCollection):
    def create_index(self, keys: list, name: str) -> str:  # type: ignore[override]
        self.calls.append((self.name, name, keys))
        return name


class _Db:
    def __init__(self, collection_type: Any, failing: str = "") -> None:
        self.calls: List[Tuple[str, str, list]] = []
        self.collection_type, self.failing = collection_type, failing
        self.collections: Dict[str, Any] = {}

    def __getitem__(self, name: str) -> Any:
        if name not in self.collections:
            kwargs = {"fail": True} if name == self.failing else {}
            self.collections[name] = self.collection_type(name, self.calls, **kwargs)
        return self.collections[name]


def test_specs_cover_the_queries_of_the_pipeline_and_use_the_profile_collection_names() -> None:
    specs = indexes.index_specs("docs_kanzlei", "chunks_kanzlei")
    by_collection: Dict[str, List[list]] = {}
    for collection, _name, keys in specs:
        by_collection.setdefault(collection, []).append(list(keys))
    assert set(by_collection) == {"docs_kanzlei", "chunks_kanzlei", "ingestion_stats", "failed_documents"}
    assert [("source", 1)] in by_collection["docs_kanzlei"], "previous version of a file"
    assert [("content_hash", 1)] in by_collection["docs_kanzlei"], "duplicate check"
    assert [("metadata.file_path", 1)] in by_collection["docs_kanzlei"]
    assert [("document_id", 1)] in by_collection["chunks_kanzlei"]
    assert [("job_id", 1), ("resolved", 1)] in by_collection["failed_documents"], "files a resumed job skips"
    assert len({(c, n) for c, n, _ in specs}) == len(specs), "index names are unique per collection"


async def test_async_ensure_creates_every_index_and_survives_a_failure() -> None:
    db = _Db(_AsyncCollection, failing="ingestion_stats")
    ensured = await indexes.ensure_ingestion_indexes(db)
    expected = [f"{c}.{n}" for c, n, _ in indexes.index_specs() if c != "ingestion_stats"]
    assert ensured == expected, "the failing collection is skipped, the rest is created"
    assert ("documents", "source_1", [("source", 1)]) in db.calls


def test_sync_ensure_matches_the_async_one() -> None:
    db = _Db(_SyncCollection)
    ensured = indexes.ensure_ingestion_indexes_sync(db, "documents", "chunks")
    assert ensured == [f"{c}.{n}" for c, n, _ in indexes.index_specs()]
