"""Tests for app/services/courses.py."""
import pytest

from app.auth import SENTINEL_USER_ID


@pytest.mark.asyncio
async def test_list_courses_empty(client, db_conn):
    from app.services import courses as svc
    result = await svc.list_courses(SENTINEL_USER_ID)
    assert result == []


@pytest.mark.asyncio
async def test_create_then_list(client, db_conn):
    from app.services import courses as svc
    from app.schemas import CourseCreate
    created = await svc.create_course(SENTINEL_USER_ID, CourseCreate(
        code="TEST101",
        full_name="Test Course",
        ects=5,
    ))
    assert created.code == "TEST101"
    assert created.full_name == "Test Course"
    result = await svc.list_courses(SENTINEL_USER_ID)
    assert len(result) == 1
    assert result[0].code == "TEST101"


@pytest.mark.asyncio
async def test_get_course_missing(client, db_conn):
    from app.services import courses as svc
    result = await svc.get_course(SENTINEL_USER_ID, "DOES_NOT_EXIST")
    assert result is None


@pytest.mark.asyncio
async def test_update_course(client, db_conn):
    from app.services import courses as svc
    from app.schemas import CourseCreate, CoursePatch
    await svc.create_course(SENTINEL_USER_ID, CourseCreate(code="UPD", full_name="Original", ects=3))
    updated = await svc.update_course(SENTINEL_USER_ID, "UPD", CoursePatch(full_name="Renamed"))
    assert updated.full_name == "Renamed"
    assert updated.code == "UPD"  # unchanged


@pytest.mark.asyncio
async def test_delete_course(client, db_conn):
    from app.services import courses as svc
    from app.schemas import CourseCreate
    await svc.create_course(SENTINEL_USER_ID, CourseCreate(code="DEL", full_name="Doomed", ects=1))
    await svc.delete_course(SENTINEL_USER_ID, "DEL")
    assert await svc.get_course(SENTINEL_USER_ID, "DEL") is None


@pytest.mark.asyncio
async def test_delete_course_cascades_owned_rows_and_preserves_audit(client, db_conn):
    from app.schemas import CourseCreate
    from app.services import courses as svc

    await svc.create_course(
        SENTINEL_USER_ID,
        CourseCreate(code="DCR", full_name="Delete Cascade Regression"),
    )
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO study_topics (user_id, course_code, name, status)
            VALUES (%s, 'DCR', 'Retryable topic', 'in_progress')
            """,
            (SENTINEL_USER_ID,),
        )
        await cur.execute(
            """
            INSERT INTO tasks (user_id, course_code, title, status)
            VALUES (%s, 'DCR', 'Practice atom', 'open')
            """,
            (SENTINEL_USER_ID,),
        )
        await cur.execute(
            """
            INSERT INTO file_index (user_id, path, course_code, size, sha256)
            VALUES (%s, %s, 'DCR', 10, 'abc')
            """,
            (SENTINEL_USER_ID, f"{SENTINEL_USER_ID}/DCR/slides.pdf"),
        )

    await svc.delete_course(SENTINEL_USER_ID, "DCR")

    async with db_conn.connection() as conn, conn.cursor() as cur:
        for table in ("courses", "study_topics", "file_index"):
            key = "code" if table == "courses" else "course_code"
            await cur.execute(
                f"SELECT count(*) AS count FROM {table} "
                f"WHERE user_id = %s AND {key} = 'DCR'",
                (SENTINEL_USER_ID,),
            )
            assert int((await cur.fetchone())["count"]) == 0, table

        await cur.execute(
            """
                SELECT kind, user_id, payload
                FROM events
                WHERE user_id = %s AND kind LIKE %s
                """,
                (SENTINEL_USER_ID, "db:delete:%"),
        )
        events = list(await cur.fetchall())

    assert any(
        event["kind"] == "db:delete:study_topics"
        and event["payload"]["before"]["course_code"] == "DCR"
        for event in events
    )

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT course_code FROM tasks WHERE user_id = %s AND title = 'Practice atom'",
            (SENTINEL_USER_ID,),
        )
        task = await cur.fetchone()
    assert task is not None
    assert task["course_code"] is None

    recreated = await svc.create_course(
        SENTINEL_USER_ID,
        CourseCreate(code="DCR", full_name="Recreated"),
    )
    assert recreated.code == "DCR"
