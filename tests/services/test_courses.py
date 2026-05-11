"""Tests for app/services/courses.py."""
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_list_courses_empty(client, db_conn):
    from app.services import courses as svc
    result = await svc.list_courses()
    assert result == []


@pytest.mark.asyncio
async def test_create_then_list(client, db_conn):
    from app.services import courses as svc
    from app.schemas import CourseCreate
    created = await svc.create_course(CourseCreate(
        code="TEST101",
        full_name="Test Course",
        ects=5,
    ))
    assert created.code == "TEST101"
    assert created.full_name == "Test Course"
    result = await svc.list_courses()
    assert len(result) == 1
    assert result[0].code == "TEST101"


@pytest.mark.asyncio
async def test_get_course_missing(client, db_conn):
    from app.services import courses as svc
    result = await svc.get_course("DOES_NOT_EXIST")
    assert result is None


@pytest.mark.asyncio
async def test_update_course(client, db_conn):
    from app.services import courses as svc
    from app.schemas import CourseCreate, CoursePatch
    await svc.create_course(CourseCreate(code="UPD", full_name="Original", ects=3))
    updated = await svc.update_course("UPD", CoursePatch(full_name="Renamed"))
    assert updated.full_name == "Renamed"
    assert updated.code == "UPD"  # unchanged


@pytest.mark.asyncio
async def test_delete_course(client, db_conn):
    from app.services import courses as svc
    from app.schemas import CourseCreate
    await svc.create_course(CourseCreate(code="DEL", full_name="Doomed", ects=1))
    await svc.delete_course("DEL")
    assert await svc.get_course("DEL") is None


@pytest.mark.asyncio
async def test_delete_course_with_dependents_and_audit_logging_can_recreate(client, db_conn):
    from app.schemas import CourseCreate
    from app.services import courses as svc

    await svc.create_course(CourseCreate(code="DCR", full_name="Delete Cascade Regression"))
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO schedule_slots (course_code, kind, weekday, start_time, end_time)
            VALUES ('DCR', 'lecture', 1, '09:00', '10:30')
            """
        )
        await cur.execute(
            """
            INSERT INTO lectures (course_code, number, held_on, kind, title)
            VALUES ('DCR', 1, '2026-05-11', 'lecture', 'Intro')
            RETURNING id
            """
        )
        lecture_id = (await cur.fetchone())["id"]
        await cur.execute(
            """
            INSERT INTO study_topics
                (course_code, lecture_id, name, status, mastery_state, retry_count, error_count)
            VALUES ('DCR', %s, 'Retryable topic', 'in_progress', 'retry_stabilization', 2, 3)
            """,
            (lecture_id,),
        )
        await cur.execute(
            """
            INSERT INTO tasks
                (course_code, title, status, mastery_state, retry_count, error_count)
            VALUES ('DCR', 'Practice atom', 'open', 'guided_practice', 1, 2)
            """
        )
        await cur.execute(
            """
            INSERT INTO deliverables (course_code, name, due_at, status)
            VALUES ('DCR', 'External sheet', %s, 'open')
            """,
            (datetime(2026, 5, 12, 12, tzinfo=timezone.utc),),
        )
        await cur.execute(
            "INSERT INTO exams (course_code, status, notes) VALUES ('DCR', 'planned', 'closed book')"
        )
        await cur.execute(
            """
            INSERT INTO file_index (path, course_code, size, sha256, text_content)
            VALUES ('DCR/slides.pdf', 'DCR', 10, 'abc', 'slides')
            """
        )
        await cur.execute(
            """
            INSERT INTO events (kind, course_code, payload)
            VALUES ('manual:note', 'DCR', '{"note":"keep audit"}'::jsonb)
            """
        )

    await svc.delete_course("DCR")

    async with db_conn.connection() as conn, conn.cursor() as cur:
        for table in (
            "courses",
            "schedule_slots",
            "lectures",
            "study_topics",
            "tasks",
            "deliverables",
            "exams",
            "file_index",
        ):
            key = "code" if table == "courses" else "course_code"
            await cur.execute(f"SELECT count(*) AS count FROM {table} WHERE {key} = 'DCR'")
            assert int((await cur.fetchone())["count"]) == 0, table

        await cur.execute(
            """
            SELECT kind, course_code, payload
            FROM events
            WHERE kind = 'manual:note' OR payload::text LIKE '%DCR%'
            ORDER BY created_at, id
            """
        )
        events = list(await cur.fetchall())

    assert events
    assert any(event["kind"] == "manual:note" for event in events)
    assert any(event["kind"] == "db:delete:tasks" for event in events)
    assert any(
        event["kind"] == "db:delete:study_topics"
        and event["payload"]["before"]["course_code"] == "DCR"
        for event in events
    )
    assert all(event["course_code"] is None for event in events)

    recreated = await svc.create_course(CourseCreate(code="DCR", full_name="Recreated"))
    assert recreated.code == "DCR"


@pytest.mark.asyncio
async def test_recreated_course_accepts_adaptive_mastery_rebuild_rows(client, db_conn):
    from datetime import date

    from app.schemas import CourseCreate, StudyTopicCreate, TaskCreate
    from app.services import agenda as agenda_svc
    from app.services import courses as courses_svc
    from app.services import study_topics as topics_svc
    from app.services import tasks as tasks_svc

    await courses_svc.create_course(CourseCreate(code="RBLD", full_name="Old rebuild target"))
    await courses_svc.delete_course("RBLD")
    await courses_svc.create_course(CourseCreate(code="RBLD", full_name="Rebuild target"))

    retry_topic = await topics_svc.create_study_topic(
        StudyTopicCreate(
            course_code="RBLD",
            name="Arrays retry unit",
            status="in_progress",
            confidence=None,
            mastery_state="retry_stabilization",
            retry_count=2,
            error_count=3,
            retry_priority=8,
            sort_order=100,
        )
    )
    new_topic = await topics_svc.create_study_topic(
        StudyTopicCreate(
            course_code="RBLD",
            name="Fresh exposure unit",
            status="not_started",
            confidence=None,
            mastery_state="not_started",
            sort_order=1,
        )
    )
    task = await tasks_svc.create_task(
        TaskCreate(
            course_code="RBLD",
            title="Undated adaptive practice atom",
            due_at=None,
            mastery_state="not_started",
            retry_count=0,
            error_count=0,
            retry_priority=0,
            tags=["curriculum", "phase:exposure"],
        )
    )

    topics = await topics_svc.list_study_topics(course_code="RBLD")
    tasks = await tasks_svc.list_tasks(course_code="RBLD")
    agenda = await agenda_svc.generate_daily_agenda(
        target_date=date(2026, 5, 11),
        course_code="RBLD",
    )

    assert {topic.id for topic in topics} == {retry_topic.id, new_topic.id}
    assert tasks == [task]
    assert task.due_at is None
    assert retry_topic.confidence is None
    assert retry_topic.mastery_state == "retry_stabilization"
    assert agenda.items[0].kind == "retry"
    assert agenda.items[0].source_ref["id"] == retry_topic.id
