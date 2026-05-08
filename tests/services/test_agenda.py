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


async def _insert_deliverable(
    db_conn,
    *,
    course_code: str = "AGEN",
    name: str,
    due_at: datetime,
    status: str = "open",
) -> None:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO deliverables (course_code, name, due_at, status)
            VALUES (%s, %s, %s, %s)
            """,
            (course_code, name, due_at, status),
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


async def _get_task_row(db_conn, title: str) -> dict:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT * FROM tasks WHERE title = %s LIMIT 1", (title,))
        row = await cur.fetchone()
        assert row is not None
        return row


async def _get_topic_row(db_conn, name: str) -> dict:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT * FROM study_topics WHERE name = %s LIMIT 1", (name,))
        row = await cur.fetchone()
        assert row is not None
        return row


async def _get_deliverable_row(db_conn, name: str) -> dict:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT * FROM deliverables WHERE name = %s LIMIT 1", (name,))
        row = await cur.fetchone()
        assert row is not None
        return row


async def _events_by_kind(db_conn, kind: str) -> list[dict]:
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT * FROM events WHERE kind = %s ORDER BY created_at DESC, id DESC",
            (kind,),
        )
        return list(await cur.fetchall())


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


@pytest.mark.asyncio
async def test_real_agenda_items_do_not_include_planning_fallback(client, db_conn):
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(db_conn, name="Only real topic", sort_order=1)

    agenda = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )

    assert agenda.items
    assert all(item.kind != "planning" for item in agenda.items)


@pytest.mark.asyncio
async def test_complete_new_concept_marks_topic_studied(client, db_conn):
    from app.schemas import AgendaActionRequest
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(db_conn, name="Binary search", sort_order=1)
    generated = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )
    item = next(item for item in generated.items if item.kind == "new_concept")

    result = await agenda_svc.complete_agenda_item(
        item.id,
        AgendaActionRequest(source_ref=item.source_ref, confidence=4),
    )

    topic = await _get_topic_row(db_conn, "Binary search")
    assert topic["status"] == "studied"
    assert topic["confidence"] == 4
    assert result.outcome == "completed"
    assert "study_topic:studied" in result.mutations_applied
    assert result.event_id is not None


@pytest.mark.asyncio
async def test_complete_task_agenda_item_marks_task_done(client, db_conn):
    from app.schemas import AgendaActionRequest
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_task(
        db_conn,
        title="Finish practice set",
        due_at=datetime(2026, 5, 8, 9, tzinfo=timezone.utc),
        priority="urgent",
    )
    generated = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )
    item = next(item for item in generated.items if item.kind == "urgent_work")

    result = await agenda_svc.complete_agenda_item(
        item.id,
        AgendaActionRequest(source_ref=item.source_ref, duration_minutes=25),
    )

    task = await _get_task_row(db_conn, "Finish practice set")
    assert task["status"] == "done"
    assert task["completed_at"] is not None
    assert "task:done" in result.mutations_applied


@pytest.mark.asyncio
async def test_complete_deliverable_records_event_without_submitting(client, db_conn):
    from app.schemas import AgendaActionRequest
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_deliverable(
        db_conn,
        name="Submit design writeup",
        due_at=datetime(2026, 5, 8, 9, tzinfo=timezone.utc),
    )
    generated = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )
    item = next(item for item in generated.items if item.kind == "urgent_work")

    result = await agenda_svc.complete_agenda_item(
        item.id,
        AgendaActionRequest(source_ref=item.source_ref, notes="reviewed locally"),
    )

    deliverable = await _get_deliverable_row(db_conn, "Submit design writeup")
    assert deliverable["status"] == "open"
    assert result.mutations_applied == []
    events = await _events_by_kind(db_conn, "agenda:completed")
    assert events[0]["payload"]["source_ref"]["type"] == "deliverable"


@pytest.mark.asyncio
async def test_complete_flashcards_records_event_without_file_mutation(
    client, db_conn, tmp_path, monkeypatch
):
    from app.schemas import AgendaActionRequest
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn, "IE")
    flashcards = tmp_path / "interview-engineering" / "resources" / "flashcards"
    flashcards.mkdir(parents=True)
    deck = flashcards / "Coding.apkg"
    deck.write_bytes(b"deck")
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))
    generated = await agenda_svc.generate_daily_agenda(target_date=date(2026, 5, 9))
    item = next(item for item in generated.items if item.kind == "flashcards")

    result = await agenda_svc.complete_agenda_item(
        item.id,
        AgendaActionRequest(source_ref=item.source_ref, duration_minutes=15),
    )

    assert deck.read_bytes() == b"deck"
    assert result.mutations_applied == []
    events = await _events_by_kind(db_conn, "agenda:completed")
    assert events
    assert events[0]["payload"]["source_ref"]["type"] == "course_files"


@pytest.mark.asyncio
async def test_complete_timed_exercise_records_result_metadata(client, db_conn):
    from app.schemas import AgendaActionRequest
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(db_conn, name="Arrays", sort_order=1)
    generated = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )
    item = next(item for item in generated.items if item.kind == "timed_exercise")

    result = await agenda_svc.complete_agenda_item(
        item.id,
        AgendaActionRequest(
            source_ref=item.source_ref,
            duration_minutes=30,
            error_count=2,
            completed_count=3,
            total_count=4,
        ),
    )

    assert result.mutations_applied == []
    events = await _events_by_kind(db_conn, "agenda:completed")
    payload = events[0]["payload"]
    assert payload["duration_minutes"] == 30
    assert payload["error_count"] == 2
    assert payload["completed_count"] == 3
    assert payload["total_count"] == 4


@pytest.mark.asyncio
async def test_log_partial_result_records_event_without_mutation(client, db_conn):
    from app.schemas import AgendaResultRequest
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(db_conn, name="Heaps", sort_order=1)
    generated = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )
    item = next(item for item in generated.items if item.kind == "new_concept")

    result = await agenda_svc.log_agenda_result(
        item.id,
        AgendaResultRequest(
            outcome="partial",
            source_ref=item.source_ref,
            confidence=2,
            duration_minutes=18,
            notes="need another pass",
        ),
    )

    topic = await _get_topic_row(db_conn, "Heaps")
    assert topic["status"] == "not_started"
    assert result.outcome == "partial"
    assert result.mutations_applied == []
    events = await _events_by_kind(db_conn, "agenda:result")
    assert events[0]["payload"]["outcome"] == "partial"
    assert events[0]["payload"]["confidence"] == 2


@pytest.mark.asyncio
async def test_skip_records_event_and_suppresses_same_day_without_mutation(client, db_conn):
    from app.schemas import AgendaActionRequest
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(db_conn, name="Queues", sort_order=1)
    generated = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )
    item = next(item for item in generated.items if item.kind == "new_concept")

    result = await agenda_svc.skip_agenda_item(
        item.id,
        AgendaActionRequest(source_ref=item.source_ref, reason="not today"),
    )
    regenerated = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )

    topic = await _get_topic_row(db_conn, "Queues")
    assert topic["status"] == "not_started"
    assert result.outcome == "skipped"
    assert all(next_item.id != item.id for next_item in regenerated.items)


@pytest.mark.asyncio
async def test_snooze_records_event_and_suppresses_until_expiry(client, db_conn):
    from app.schemas import AgendaActionRequest
    from app.services import agenda as agenda_svc

    await _seed_course(db_conn)
    await _insert_topic(db_conn, name="Stacks", sort_order=1)
    generated = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
    )
    item = next(item for item in generated.items if item.kind == "new_concept")

    result = await agenda_svc.snooze_agenda_item(
        item.id,
        AgendaActionRequest(
            source_ref=item.source_ref,
            snooze_until=datetime(2026, 5, 9, 18, tzinfo=timezone.utc),
            reason="after work",
        ),
        now=datetime(2026, 5, 9, 12, tzinfo=timezone.utc),
    )
    suppressed = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
        now=datetime(2026, 5, 9, 12, tzinfo=timezone.utc),
    )
    after_expiry = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 9),
        course_code="AGEN",
        now=datetime(2026, 5, 9, 19, tzinfo=timezone.utc),
    )

    assert result.outcome == "snoozed"
    assert all(next_item.id != item.id for next_item in suppressed.items)
    assert any(next_item.id == item.id for next_item in after_expiry.items)


@pytest.mark.asyncio
async def test_omitted_date_uses_configured_local_timezone(client, db_conn, monkeypatch):
    from datetime import datetime as real_datetime

    from app.schemas import AppSettingsPatch
    from app.services import agenda as agenda_svc
    from app.services import settings as settings_svc

    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = real_datetime(2026, 5, 8, 22, 30, tzinfo=timezone.utc)
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

    await settings_svc.update_settings(AppSettingsPatch(timezone="Africa/Nairobi"))
    monkeypatch.setattr(agenda_svc, "datetime", FixedDateTime)

    generated = await agenda_svc.generate_daily_agenda()

    assert generated.date == date(2026, 5, 9)
