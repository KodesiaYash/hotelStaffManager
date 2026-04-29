from __future__ import annotations

from pathlib import Path


def test_close_task_query_casts_resolution_note_to_text() -> None:
    store_path = (
        Path(__file__).resolve().parents[4]
        / "controlplane"
        / "boundary"
        / "storageInterface"
        / "memory"
        / "postgres_store.py"
    )
    source = store_path.read_text(encoding="utf-8")

    assert "%(resolution_note)s::text IS NULL" in source
    assert "|| %(resolution_note)s::text" in source
