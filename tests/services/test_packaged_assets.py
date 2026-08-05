from __future__ import annotations

from pathlib import Path

import pytest


def _write_manifest(path: Path) -> None:
    path.write_text(
        """
sources:
  - id: computer-science-flash-cards
    path: curriculum/sources/computer-science-flash-cards
assets:
  - id: ciu-flashcards-standard
    format: db
    source:
      repo: computer-science-flash-cards
      path: cards-jwasham.db
  - id: ignored-notebook
    format: ipynb
    source:
      repo: computer-science-flash-cards
      path: ignored.ipynb
""".lstrip(),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_sync_packaged_assets_into_study_root(tmp_path, monkeypatch):
    from app.auth import SENTINEL_USER_ID
    from app.services import packaged_assets as packaged_assets_svc
    from app.services import storage as storage_svc

    manifest_path = tmp_path / "manifest.yaml"
    sources_root = tmp_path / "curriculum" / "sources"
    source_dir = sources_root / "computer-science-flash-cards"
    study_root = tmp_path / "courses"
    source_dir.mkdir(parents=True)
    (source_dir / "cards-jwasham.db").write_bytes(b"flashcards")
    _write_manifest(manifest_path)
    monkeypatch.setenv("STUDY_ROOT", str(study_root))

    summary = packaged_assets_svc.sync_packaged_curriculum_assets(
        user_id=SENTINEL_USER_ID,
        manifest_path=manifest_path,
        sources_root=sources_root,
        study_root=study_root,
    )

    assert summary["copied"] == 1
    assert summary["skipped"] == 0
    assert summary["missing"] == []
    destination = (
        study_root
        / str(SENTINEL_USER_ID)
        / "interview-engineering"
        / "resources"
        / "flashcards"
        / "cards-jwasham.db"
    )
    assert destination.read_bytes() == b"flashcards"

    root_entries = await storage_svc.list_files(SENTINEL_USER_ID, "")
    assert [entry["name"] for entry in root_entries] == ["interview-engineering"]
    flashcard_entries = await storage_svc.list_files(
        SENTINEL_USER_ID,
        "interview-engineering/resources/flashcards"
    )
    assert [entry["name"] for entry in flashcard_entries] == ["cards-jwasham.db"]
    assert await storage_svc.download(
        SENTINEL_USER_ID,
        "interview-engineering/resources/flashcards/cards-jwasham.db"
    ) == b"flashcards"


def test_sync_packaged_assets_is_idempotent(tmp_path):
    from app.auth import SENTINEL_USER_ID
    from app.services import packaged_assets as packaged_assets_svc

    manifest_path = tmp_path / "manifest.yaml"
    sources_root = tmp_path / "curriculum" / "sources"
    source_dir = sources_root / "computer-science-flash-cards"
    study_root = tmp_path / "courses"
    source_dir.mkdir(parents=True)
    (source_dir / "cards-jwasham.db").write_bytes(b"flashcards")
    _write_manifest(manifest_path)

    first = packaged_assets_svc.sync_packaged_curriculum_assets(
        user_id=SENTINEL_USER_ID,
        manifest_path=manifest_path,
        sources_root=sources_root,
        study_root=study_root,
    )
    second = packaged_assets_svc.sync_packaged_curriculum_assets(
        user_id=SENTINEL_USER_ID,
        manifest_path=manifest_path,
        sources_root=sources_root,
        study_root=study_root,
    )

    assert first["copied"] == 1
    assert second["copied"] == 0
    assert second["skipped"] == 1
