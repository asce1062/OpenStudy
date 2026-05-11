from typing import List
from .. import db
from ..schemas import Course, CourseCreate, CoursePatch
from ._helpers import validated_cols


async def list_courses() -> List[Course]:
    rows = await db.fetch("SELECT * FROM courses ORDER BY code")
    return [Course.model_validate(r) for r in rows]


async def get_course(code: str) -> Course | None:
    row = await db.fetchrow(
        "SELECT * FROM courses WHERE code = %s LIMIT 1",
        code,
    )
    return Course.model_validate(row) if row else None


async def create_course(body: CourseCreate) -> Course:
    data = body.model_dump(mode="json", exclude_none=True)
    cols = validated_cols(CourseCreate, data)
    placeholders = ", ".join(["%s"] * len(cols))
    row = await db.fetchrow(
        f"INSERT INTO courses ({', '.join(cols)}) "
        f"VALUES ({placeholders}) RETURNING *",
        *[data[c] for c in cols],
    )
    if row is None:
        raise ValueError(f"failed to create course {body.code}")
    return Course.model_validate(row)


async def update_course(code: str, patch: CoursePatch) -> Course:
    data = patch.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    if not data:
        course = await get_course(code)
        if not course:
            raise ValueError(f"course {code} not found")
        return course
    cols = validated_cols(CoursePatch, data)
    set_clause = ", ".join(f"{c} = %s" for c in cols)
    row = await db.fetchrow(
        f"UPDATE courses SET {set_clause} WHERE code = %s RETURNING *",
        *[data[c] for c in cols], code,
    )
    if row is None:
        raise ValueError(f"course {code} not found")
    return Course.model_validate(row)


async def delete_course(code: str) -> None:
    async with db.db() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM file_index WHERE course_code = %s", (code,))
                await cur.execute("DELETE FROM tasks WHERE course_code = %s", (code,))
                await cur.execute("DELETE FROM study_topics WHERE course_code = %s", (code,))
                await cur.execute("DELETE FROM lectures WHERE course_code = %s", (code,))
                await cur.execute("DELETE FROM deliverables WHERE course_code = %s", (code,))
                await cur.execute("DELETE FROM schedule_slots WHERE course_code = %s", (code,))
                await cur.execute("DELETE FROM exams WHERE course_code = %s", (code,))
                await cur.execute("DELETE FROM courses WHERE code = %s", (code,))
