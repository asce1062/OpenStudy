from __future__ import annotations

import json
from typing import Any

import pytest

import app.db as db_module
from scripts.curriculum.seed_openstudy import (
    REPO_ROOT,
    read_manifest,
    seed_manifest_data,
)


def sample_manifest() -> dict[str, Any]:
    return {
        "meta": {
            "id": "ie-curriculum-interview-engineering",
            "name": "Interview Engineering",
            "schema_version": 2,
            "timezone": "Africa/Nairobi",
        },
        "sources": [
            {
                "id": "coding-interview-university",
                "name": "Coding Interview University",
                "path": "curriculum/sources/coding-interview-university",
                "upstream": "https://github.com/jwasham/coding-interview-university",
            }
        ],
        "assets": [
            {
                "id": "icc-anki-coding",
                "title": "Interactive Coding Challenges Coding Anki Deck",
                "kind": "flashcard-deck",
                "format": "apkg",
                "course_code": "ICC",
                "source": {
                    "repo": "interactive-coding-challenges",
                    "path": "anki_cards/Coding.apkg",
                },
                "storage_path": "ICC/flashcards/Coding.apkg",
                "usage": {
                    "openstudy_readable": False,
                    "import_into_anki": True,
                    "review_cadence": "weekly",
                },
            }
        ],
        "tracks": [
            {
                "id": "ie-track-core",
                "name": "Interview Engineering Core",
                "description": "Core track",
                "suggested_order": 1,
                "depends_on": [],
                "modules": [
                    {
                        "id": "ie-module-foundations",
                        "name": "Foundations",
                        "description": "Study setup",
                        "difficulty": {"level": "beginner", "score": 1},
                        "estimated_effort_band": "light",
                        "expected_retry_density": "medium",
                        "cognitive_load": "medium",
                        "decay_risk": "medium",
                        "interview_frequency": "medium",
                        "current_mastery_state": "not_started",
                        "tags": ["setup"],
                        "prerequisites": [],
                        "depends_on": [],
                        "suggested_order": 1,
                        "lessons": [
                            {
                                "id": "ciu-first-lesson",
                                "title": "First Lesson",
                                "type": {
                                    "category": "reading",
                                    "medium": "markdown",
                                    "interaction": "passive",
                                },
                                "source": {
                                    "repo": "coding-interview-university",
                                    "path": "README.md",
                                },
                                "estimated_minutes": 30,
                                "completion_criteria": "Read it.",
                                "suggested_order": 1,
                                "depends_on": [],
                                "difficulty": {"level": "beginner", "score": 1},
                                "cognitive_load": "low",
                            },
                            {
                                "id": "ciu-second-lesson",
                                "title": "Second Lesson",
                                "type": {
                                    "category": "checkpoint",
                                    "medium": "manifest",
                                    "interaction": "reflective",
                                },
                                "source": {
                                    "repo": "manifest",
                                    "path": "curriculum/test.yaml",
                                },
                                "estimated_minutes": 60,
                                "completion_criteria": "Explain it.",
                                "suggested_order": 2,
                                "depends_on": ["ciu-first-lesson"],
                                "difficulty": {"level": "beginner", "score": 1},
                                "cognitive_load": "medium",
                                "inferred": True,
                            },
                            {
                                "id": "ciu-second-lesson-extra",
                                "title": "Second Lesson Extra",
                                "type": {
                                    "category": "checkpoint",
                                    "medium": "manifest",
                                    "interaction": "reflective",
                                },
                                "source": {
                                    "repo": "manifest",
                                    "path": "curriculum/test.yaml",
                                },
                                "estimated_minutes": 30,
                                "completion_criteria": "Explain the extension.",
                                "suggested_order": 3,
                                "depends_on": ["ciu-second-lesson"],
                                "difficulty": {"level": "beginner", "score": 2},
                                "cognitive_load": "medium",
                                "inferred": True,
                            },
                        ],
                    }
                ],
            }
        ],
    }


async def table_count(db_conn: Any, table: str) -> int:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(f"SELECT count(*) AS count FROM {table}")
        row = await cur.fetchone()
    return int(row["count"])


def use_test_db(db_conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_module, "_pool", db_conn)


async def test_read_manifest_loads_generated_manifest() -> None:
    manifest = read_manifest(REPO_ROOT / "curriculum/interview_manifest.v2.yaml")

    assert manifest["meta"]["schema_version"] == 2
    assert len(manifest["assets"]) == 6


async def test_dry_run_does_not_mutate_db(
    db_conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_test_db(db_conn, monkeypatch)

    plan = await seed_manifest_data(sample_manifest(), dry_run=True)

    assert plan.lessons.create == 3
    assert await table_count(db_conn, "courses") == 0
    assert await table_count(db_conn, "study_topics") == 0
    assert await table_count(db_conn, "tasks") == 0
    assert await table_count(db_conn, "events") == 0


async def test_seed_is_idempotent(db_conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    use_test_db(db_conn, monkeypatch)

    first = await seed_manifest_data(sample_manifest())
    second = await seed_manifest_data(sample_manifest())

    assert first.lessons.create == 3
    assert first.lessons.update == 0
    assert second.lessons.update == 3
    assert await table_count(db_conn, "courses") == 1
    assert await table_count(db_conn, "study_topics") == 1
    assert await table_count(db_conn, "tasks") == 3


async def test_assets_are_represented_as_events(
    db_conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_test_db(db_conn, monkeypatch)

    await seed_manifest_data(sample_manifest())

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT payload FROM events WHERE kind = 'curriculum:asset'")
        row = await cur.fetchone()

    assert row is not None
    assert row["payload"]["seed_id"] == "icc-anki-coding"
    assert row["payload"]["asset"]["usage"]["review_cadence"] == "weekly"


async def test_dependencies_are_preserved_in_task_metadata(
    db_conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_test_db(db_conn, monkeypatch)

    await seed_manifest_data(sample_manifest())

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT description FROM tasks WHERE title = 'Second Lesson'")
        row = await cur.fetchone()

    assert row is not None
    metadata = json.loads(row["description"].split("\n", 1)[1])
    assert metadata["depends_on"] == ["ciu-first-lesson"]
    assert metadata["suggested_order_semantics"] == "weak_tiebreaker"


async def test_seed_preserves_adaptive_mastery_metadata(
    db_conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_test_db(db_conn, monkeypatch)

    await seed_manifest_data(sample_manifest())

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT mastery_state, notes FROM study_topics WHERE name = 'Foundations'"
        )
        module_row = await cur.fetchone()
        await cur.execute(
            "SELECT mastery_state, retry_count, error_count, retry_priority, description "
            "FROM tasks WHERE title = 'First Lesson'"
        )
        lesson_row = await cur.fetchone()

    assert module_row is not None
    assert lesson_row is not None
    assert module_row["mastery_state"] == "not_started"
    assert lesson_row["mastery_state"] == "not_started"
    assert lesson_row["retry_count"] == 0
    assert lesson_row["error_count"] == 0
    assert lesson_row["retry_priority"] == 0

    module_metadata = json.loads(module_row["notes"].split("\n", 1)[1])
    lesson_metadata = json.loads(lesson_row["description"].split("\n", 1)[1])
    assert module_metadata["module"]["estimated_effort_band"] == "light"
    assert module_metadata["module"]["current_mastery_state"] == "not_started"
    assert lesson_metadata["mastery_state"] == "not_started"
    assert lesson_metadata["retry_metadata"]["retry_count"] == 0


async def test_missing_asset_file_produces_warning(
    db_conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_test_db(db_conn, monkeypatch)

    manifest = sample_manifest()
    manifest["assets"][0]["source"]["path"] = "missing/Coding.apkg"

    plan = await seed_manifest_data(manifest, dry_run=True)

    assert any("source file is missing" in warning for warning in plan.warnings)
