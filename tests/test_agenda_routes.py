from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_today_agenda_route_returns_agenda(db_conn, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    import app.db as db_module
    from app.auth import require_auth
    from app.config import get_settings
    from app.main import create_app

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO courses (code, full_name) VALUES ('APIA', 'API Agenda')"
        )
        await cur.execute(
            """
            INSERT INTO study_topics (course_code, name, status, sort_order)
            VALUES ('APIA', 'API topic', 'not_started', 1)
            """
        )

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    get_settings.cache_clear()
    monkeypatch.setattr(db_module, "_pool", db_conn)
    app = create_app()
    app.dependency_overrides[require_auth] = lambda: True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/agenda/today", params={"course_code": "APIA"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["course_code"] == "APIA"
    assert body["items"]
    assert all("reason" in item for item in body["items"])
