from typing import Optional

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..schemas import (
    AgendaActionRequest,
    AgendaActionResponse,
    AgendaResultRequest,
    DailyAgenda,
)
from ..services import agenda as agenda_svc

router = APIRouter(prefix="/agenda", tags=["agenda"])


@router.get("/today", response_model=DailyAgenda)
async def today(
    course_code: Optional[str] = None,
    _: bool = Depends(require_auth),
) -> DailyAgenda:
    return await agenda_svc.generate_daily_agenda(course_code=course_code)


@router.post("/items/{agenda_item_id}/complete", response_model=AgendaActionResponse)
async def complete_item(
    agenda_item_id: str,
    body: AgendaActionRequest,
    include_agenda: bool = False,
    _: bool = Depends(require_auth),
) -> AgendaActionResponse:
    return await agenda_svc.complete_agenda_item(
        agenda_item_id,
        body,
        include_agenda=include_agenda,
    )


@router.post("/items/{agenda_item_id}/skip", response_model=AgendaActionResponse)
async def skip_item(
    agenda_item_id: str,
    body: AgendaActionRequest,
    include_agenda: bool = False,
    _: bool = Depends(require_auth),
) -> AgendaActionResponse:
    return await agenda_svc.skip_agenda_item(
        agenda_item_id,
        body,
        include_agenda=include_agenda,
    )


@router.post("/items/{agenda_item_id}/snooze", response_model=AgendaActionResponse)
async def snooze_item(
    agenda_item_id: str,
    body: AgendaActionRequest,
    include_agenda: bool = False,
    _: bool = Depends(require_auth),
) -> AgendaActionResponse:
    return await agenda_svc.snooze_agenda_item(
        agenda_item_id,
        body,
        include_agenda=include_agenda,
    )


@router.post("/items/{agenda_item_id}/result", response_model=AgendaActionResponse)
async def log_result(
    agenda_item_id: str,
    body: AgendaResultRequest,
    include_agenda: bool = False,
    _: bool = Depends(require_auth),
) -> AgendaActionResponse:
    return await agenda_svc.log_agenda_result(
        agenda_item_id,
        body,
        include_agenda=include_agenda,
    )
