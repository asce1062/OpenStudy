"""End-to-end integration tests for the routes that aggregate multiple
services. These exist specifically to catch the "forgot to await" class of
bug introduced when migrating callers to async — a missed await would
serialize as a coroutine repr and either 500 or fail a Pydantic shape
check, which is exactly what these tests assert against.

`/api/health` exercises `db.fetchval` + `storage_svc.list_files`.
`/api/dashboard` exercises every list_* service through `dashboard.py`.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok_with_db_and_storage(client, db_conn, tmp_path, monkeypatch):
    """Health check must report db=ok and storage=ok with no `coroutine` reprs."""
    monkeypatch.setenv("STUDY_ROOT", str(tmp_path))
    resp = await client.get("/api/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, body
    assert body["db"] == "ok", body
    assert body["storage"].startswith("ok "), body


@pytest.mark.asyncio
async def test_dashboard_aggregates_every_service(db_conn, monkeypatch):
    """The dashboard route must return the full DashboardSummary shape with
    every list field present and Pydantic-valid — no awaited-but-uninvoked
    coroutines slipping into the response."""
    from httpx import ASGITransport, AsyncClient

    import app.db as db_module
    from app.auth import require_auth
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    get_settings.cache_clear()
    monkeypatch.setattr(db_module, "_pool", db_conn)
    app = create_app()
    # Bypass the cookie-signed auth — Depends(require_auth) is short-circuited
    # to always return True for the duration of this test.
    app.dependency_overrides[require_auth] = lambda: True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Empty DB: every list is [], plus a `now` timestamp + an empty fall_behind.
    for key in (
        "courses", "slots", "exams", "deliverables",
        "tasks", "study_topics", "lectures", "fall_behind",
    ):
        assert isinstance(body.get(key), list), (key, body.get(key))
    assert "now" in body and isinstance(body["now"], str)


@pytest.mark.asyncio
async def test_files_api_serves_synced_packaged_assets(db_conn, tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    import app.db as db_module
    from app.auth import require_auth
    from app.config import get_settings
    from app.main import create_app
    from app.services import packaged_assets as packaged_assets_svc

    manifest_path = tmp_path / "manifest.yaml"
    sources_root = tmp_path / "curriculum" / "sources"
    source_dir = sources_root / "interactive-coding-challenges" / "anki_cards"
    study_root = tmp_path / "courses"
    source_dir.mkdir(parents=True)
    (source_dir / "Coding.apkg").write_bytes(b"anki deck")
    manifest_path.write_text(
        """
sources:
  - id: interactive-coding-challenges
    path: curriculum/sources/interactive-coding-challenges
assets:
  - id: icc-anki-coding
    format: apkg
    source:
      repo: interactive-coding-challenges
      path: anki_cards/Coding.apkg
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("STUDY_ROOT", str(study_root))
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    get_settings.cache_clear()
    packaged_assets_svc.sync_packaged_curriculum_assets(
        manifest_path=manifest_path,
        sources_root=sources_root,
        study_root=study_root,
    )

    monkeypatch.setattr(db_module, "_pool", db_conn)
    app = create_app()
    app.dependency_overrides[require_auth] = lambda: True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        listing = await ac.get(
            "/api/files/list",
            params={"prefix": "interview-engineering/resources/flashcards"},
        )
        download = await ac.get(
            "/api/files/raw",
            params={"path": "interview-engineering/resources/flashcards/Coding.apkg"},
        )

    assert listing.status_code == 200, listing.text
    assert listing.json()[0]["path"] == "interview-engineering/resources/flashcards/Coding.apkg"
    assert download.status_code == 200, download.text
    assert download.content == b"anki deck"


@pytest.mark.asyncio
async def test_login_flow_uses_async_db_paths(client, db_conn, monkeypatch):
    """Drive /auth/login through the async ratelimit + auth.get_totp_state
    paths. Expects a 401 (no password configured, but the request must reach
    that error rather than 500-ing on a missed await)."""
    # No app_password_hash configured → verify_password returns False → 401.
    resp = await client.post("/api/auth/login", json={"password": "irrelevant"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "invalid password"
