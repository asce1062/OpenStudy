"""Tests for deterministic daily agenda generation."""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest


async def _seed_course(db_conn, code: str = "AGEN") -> None:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO courses (code, full_name, folder_name) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (code, f"Agenda course {code}", code),
        )


async def _insert_topic(
    db_conn,
    *,
    course_code: str = "AGEN",
    name: str,
    status: str = "not_started",
    covered_on: date | None = None,
    confidence: int | None = None,
    sort_order: int = 0,
) -> None:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO study_topics
                (course_code, name, status, covered_on, confidence, sort_order)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (course_code, name, status, covered_on, confidence, sort_order),
        )


async def _insert_task(
    db_conn,
    *,
    course_code: str | None = "AGEN",
    title: str,
    due_at: datetime | None = None,
    priority: str = "med",
    status: str = "open",
) -> None:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO tasks (course_code, title, due_at, priority, status)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (course_code, title, due_at, priority, status),
        )


async def _insert_slot(
    db_conn,
    *,
    course_code: str = "AGEN",
    weekday: int,
    start_time: time = time(9, 0),
    end_time: time = time(10, 30),
) -> None:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO schedule_slots
                (course_code, kind, weekday, start_time, end_time)
            VALUES (%s, 'lecture', %s, %s, %s)
            """,
            (course_code, weekday, start_time, end_time),
        )


@pytest.mark.asyncio
async def test_agenda_puts_overdue_work_before_new_concept(client, db_conn):
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_task(
        db_conn,
        title="Submit overdue worksheet",
        due_at=datetime(2026, 5, 8, 9, tzinfo=timezone.utc),
        priority="high",
    )
    await _insert_topic(db_conn, name="New graph traversal", sort_order=1)

    agenda = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )

    assert agenda.items[0].kind == "urgent_work"
    assert agenda.items[0].title == "Submit overdue worksheet"
    assert [item.kind for item in agenda.items].index("urgent_work") < [
        item.kind for item in agenda.items
    ].index("new_concept")


@pytest.mark.asyncio
async def test_agenda_prioritizes_struggling_topics(client, db_conn):
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(
        db_conn,
        name="Dynamic programming recurrence",
        status="struggling",
        confidence=1,
        sort_order=1,
    )
    await _insert_topic(db_conn, name="Fresh hashing concept", sort_order=2)

    agenda = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )

    assert agenda.items[0].kind == "struggling_topic"
    assert "struggling" in agenda.items[0].reason.lower()


@pytest.mark.asyncio
async def test_fall_behind_warning_increases_review_priority(client, db_conn):
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(
        db_conn,
        name="Unreviewed lecture backlog",
        status="not_started",
        covered_on=date(2026, 5, 1),
        sort_order=1,
    )
    # May 10, 2026 is a Sunday; the next lecture is within 48 hours.
    await _insert_slot(db_conn, weekday=7)

    agenda = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )

    review = next(item for item in agenda.items if item.kind == "review")
    assert review.priority >= 80
    assert "fall-behind" in review.reason.lower()


@pytest.mark.asyncio
async def test_flashcard_assets_create_flashcard_agenda_item(
    client, db_conn, tmp_path, monkeypatch
):
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn, "IE")
    flashcards = tmp_path / "interview-engineering" / "resources" / "flashcards"
    flashcards.mkdir(parents=True)
    (flashcards / "Coding.apkg").write_bytes(b"deck")
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))

    agenda = await agenda_svc.generate_daily_agenda(target_date=date(2026, 5, 9))

    item = next(item for item in agenda.items if item.kind == "flashcards")
    assert item.title == "Review Interview Engineering flashcards"
    assert item.source_ref["path"] == "interview-engineering/resources/flashcards"


@pytest.mark.asyncio
async def test_agenda_generation_is_deterministic(client, db_conn):
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(db_conn, name="Arrays", sort_order=1)
    await _insert_topic(db_conn, name="Linked lists", sort_order=2)
    await _insert_task(
        db_conn,
        title="Practice two array problems",
        due_at=datetime(2026, 5, 9, 10, tzinfo=timezone.utc),
        priority="urgent",
    )

    first = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )
    second = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@pytest.mark.asyncio
async def test_empty_state_returns_useful_agenda(client, db_conn):
    from app.services import agenda as agenda_svc

    agenda = await agenda_svc.generate_daily_agenda(target_date=date(2026, 5, 9))

    assert agenda.items
    assert agenda.items[0].kind == "planning"
    assert "Add a course" in agenda.items[0].title
