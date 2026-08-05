from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_today_agenda_route_returns_agenda(db_conn, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    import app.db as db_module
    from app.auth import SENTINEL_USER_ID, _sentinel_user, require_user
    from app.config import get_settings
    from app.main import create_app

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO courses (user_id, code, full_name) VALUES (%s, 'APIA', 'API Agenda')",
            (SENTINEL_USER_ID,),
        )
        await cur.execute(
            """
            INSERT INTO study_topics (user_id, course_code, name, status, sort_order)
            VALUES (%s, 'APIA', 'API topic', 'not_started', 1)
            """,
            (SENTINEL_USER_ID,),
        )

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    get_settings.cache_clear()
    monkeypatch.setattr(db_module, "_pool", db_conn)
    app = create_app()
    app.dependency_overrides[require_user] = _sentinel_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/agenda/today", params={"course_code": "APIA"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["course_code"] == "APIA"
    assert body["items"]
    assert all("reason" in item for item in body["items"])


@pytest.mark.asyncio
async def test_complete_agenda_item_route_can_return_refreshed_agenda(db_conn, monkeypatch):
    from datetime import date

    from httpx import ASGITransport, AsyncClient

    import app.db as db_module
    from app.auth import SENTINEL_USER_ID, _sentinel_user, require_user
    from app.config import get_settings
    from app.main import create_app
    from app.services import agenda as agenda_svc

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO courses (user_id, code, full_name) VALUES (%s, 'APIB', 'API Agenda Actions')",
            (SENTINEL_USER_ID,),
        )
        await cur.execute(
            """
            INSERT INTO study_topics (user_id, course_code, name, status, sort_order)
            VALUES (%s, 'APIB', 'Route topic', 'not_started', 1)
            """,
            (SENTINEL_USER_ID,),
        )

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    get_settings.cache_clear()
    monkeypatch.setattr(db_module, "_pool", db_conn)

    generated = await agenda_svc.generate_daily_agenda(
        SENTINEL_USER_ID,
        target_date=date(2026, 5, 9),
        course_code="APIB",
    )
    item = next(item for item in generated.items if item.kind == "new_concept")

    app = create_app()
    app.dependency_overrides[require_user] = _sentinel_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            f"/api/agenda/items/{item.id}/complete",
            params={"include_agenda": "true"},
            json={"source_ref": item.source_ref, "confidence": 3},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "completed"
    assert "study_topic:mastery_state" in body["mutations_applied"]
    assert body["refresh_recommended"] is True
    assert body["agenda"] is not None

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT status, mastery_state FROM study_topics WHERE name = 'Route topic'")
        topic = await cur.fetchone()

    assert topic["status"] == "in_progress"
    assert topic["mastery_state"] == "independent_practice"
