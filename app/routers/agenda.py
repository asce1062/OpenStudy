from typing import Optional

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..schemas import DailyAgenda
from ..services import agenda as agenda_svc

router = APIRouter(prefix="/agenda", tags=["agenda"])


@router.get("/today", response_model=DailyAgenda)
async def today(
    course_code: Optional[str] = None,
    _: bool = Depends(require_auth),
) -> DailyAgenda:
    return await agenda_svc.generate_daily_agenda(course_code=course_code)
