from __future__ import annotations

import pytest

from tests.mcp._harness import get_tool_fn


@pytest.mark.asyncio
async def test_generate_daily_agenda_tool_returns_items(client, db_conn, mcp_server):
    async with db_conn.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO courses (code, full_name) VALUES ('MCPA', 'MCP Agenda')"
        )
        await cur.execute(
            """
            INSERT INTO study_topics (course_code, name, status, sort_order)
            VALUES ('MCPA', 'MCP topic', 'not_started', 1)
            """
        )

    generate_daily_agenda = get_tool_fn(mcp_server, "generate_daily_agenda")
    result = await generate_daily_agenda(date="2026-05-09", course_code="MCPA")

    assert result["date"] == "2026-05-09"
    assert result["course_code"] == "MCPA"
    assert result["items"]
    assert any(item["kind"] == "new_concept" for item in result["items"])
