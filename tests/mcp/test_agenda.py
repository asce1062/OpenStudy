from __future__ import annotations

import pytest
from app.auth import SENTINEL_USER_ID

from tests.mcp._harness import get_tool_fn


@pytest.mark.asyncio
async def test_generate_daily_agenda_tool_returns_items(client, db_conn, mcp_server):
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO courses (user_id, code, full_name) VALUES (%s, 'MCPA', 'MCP Agenda')",
            (SENTINEL_USER_ID,),
        )
        await cur.execute(
            """
            INSERT INTO study_topics (user_id, course_code, name, status, sort_order)
            VALUES (%s, 'MCPA', 'MCP topic', 'not_started', 1)
            """,
            (SENTINEL_USER_ID,),
        )

    generate_daily_agenda = get_tool_fn(mcp_server, "generate_daily_agenda")
    result = await generate_daily_agenda(date="2026-05-09", course_code="MCPA")

    assert result["date"] == "2026-05-09"
    assert result["course_code"] == "MCPA"
    assert result["items"]
    assert any(item["kind"] == "new_concept" for item in result["items"])


@pytest.mark.asyncio
async def test_complete_agenda_item_tool_updates_source(client, db_conn, mcp_server):
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO courses (user_id, code, full_name) VALUES (%s, 'MCPB', 'MCP Agenda Actions')",
            (SENTINEL_USER_ID,),
        )
        await cur.execute(
            """
            INSERT INTO study_topics (user_id, course_code, name, status, sort_order)
            VALUES (%s, 'MCPB', 'MCP action topic', 'not_started', 1)
            """,
            (SENTINEL_USER_ID,),
        )

    generate_daily_agenda = get_tool_fn(mcp_server, "generate_daily_agenda")
    complete_agenda_item = get_tool_fn(mcp_server, "complete_agenda_item")

    generated = await generate_daily_agenda(date="2026-05-09", course_code="MCPB")
    item = next(item for item in generated["items"] if item["kind"] == "new_concept")
    completed = await complete_agenda_item(
        agenda_item_id=item["id"],
        source_ref=item["source_ref"],
        confidence=5,
        duration_minutes=20,
        notes="clear now",
    )

    assert completed["outcome"] == "completed"
    assert "study_topic:studied" in completed["mutations_applied"]

    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT status, confidence FROM study_topics WHERE name = 'MCP action topic'"
        )
        row = await cur.fetchone()
    assert row["status"] == "studied"
    assert row["confidence"] == 5


@pytest.mark.asyncio
async def test_skip_and_snooze_agenda_item_tools_record_events(client, db_conn, mcp_server):
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO courses (user_id, code, full_name) VALUES (%s, 'MCPC', 'MCP Agenda Events')",
            (SENTINEL_USER_ID,),
        )
        await cur.execute(
            """
            INSERT INTO study_topics (user_id, course_code, name, status, sort_order)
            VALUES (%s, 'MCPC', 'MCP skip topic', 'not_started', 1)
            """,
            (SENTINEL_USER_ID,),
        )

    generate_daily_agenda = get_tool_fn(mcp_server, "generate_daily_agenda")
    skip_agenda_item = get_tool_fn(mcp_server, "skip_agenda_item")
    snooze_agenda_item = get_tool_fn(mcp_server, "snooze_agenda_item")

    generated = await generate_daily_agenda(date="2026-05-09", course_code="MCPC")
    item = next(item for item in generated["items"] if item["kind"] == "new_concept")
    skipped = await skip_agenda_item(
        agenda_item_id=item["id"],
        source_ref=item["source_ref"],
        reason="later",
    )
    snoozed = await snooze_agenda_item(
        agenda_item_id=item["id"],
        source_ref=item["source_ref"],
        snooze_minutes=30,
        reason="break",
    )

    assert skipped["outcome"] == "skipped"
    assert snoozed["outcome"] == "snoozed"
