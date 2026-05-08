"""Deterministic daily agenda generation.

The agenda engine is intentionally explainable: it gathers existing OpenStudy
state, builds candidate actions with fixed scores, and returns the highest
value 4-6 items in a stable category order. It does not call an LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..schemas import (
    AgendaActionRequest,
    AgendaActionResponse,
    AgendaItem,
    AgendaResultRequest,
    Course,
    DailyAgenda,
    Deliverable,
    Exam,
    EventCreate,
    FallBehindItem,
    StudyTopic,
    StudyTopicPatch,
    Task,
)
from . import (
    courses as courses_svc,
    deliverables as deliverables_svc,
    events as events_svc,
    exams as exams_svc,
    fall_behind as fall_behind_svc,
    lectures as lectures_svc,
    slots as slots_svc,
    settings as settings_svc,
    storage as storage_svc,
    study_topics as topics_svc,
    tasks as tasks_svc,
)

FLASHCARD_PREFIX = "interview-engineering/resources/flashcards"
AGENDA_MIN_ITEMS = 4
AGENDA_MAX_ITEMS = 6
ACTION_EVENT_KIND = {
    "completed": "agenda:completed",
    "partial": "agenda:result",
    "failed": "agenda:result",
    "skipped": "agenda:skipped",
    "snoozed": "agenda:snoozed",
}


@dataclass(frozen=True)
class _Candidate:
    item: AgendaItem
    category_order: int


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "item"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _end_of_day(target_date: date) -> datetime:
    return datetime.combine(target_date, time(23, 59, 59), tzinfo=timezone.utc)


async def _local_now() -> datetime:
    settings = await settings_svc.get_settings()
    try:
        tz = ZoneInfo(settings.timezone or "UTC")
    except Exception:
        tz = timezone.utc
    return datetime.now(tz)


def _course_filter(items: list[Any], course_code: str | None) -> list[Any]:
    if not course_code:
        return items
    return [item for item in items if getattr(item, "course_code", None) == course_code]


def _course_name_by_code(courses: list[Course]) -> dict[str, str]:
    return {course.code: course.short_name or course.full_name for course in courses}


def _agenda_item(
    *,
    target_date: date,
    kind: str,
    title: str,
    course_code: str | None,
    reason: str,
    estimated_minutes: int,
    priority: int,
    source_ref: dict[str, Any],
) -> AgendaItem:
    source_key = str(source_ref.get("id") or source_ref.get("path") or title)
    return AgendaItem(
        id=f"agenda-{target_date.isoformat()}-{kind}-{_slug(source_key)}",
        kind=kind,
        title=title,
        course_code=course_code,
        reason=reason,
        estimated_minutes=estimated_minutes,
        priority=max(1, min(100, priority)),
        source_ref=source_ref,
    )


def parse_agenda_item_date(agenda_item_id: str) -> date | None:
    match = re.match(r"^agenda-(\d{4}-\d{2}-\d{2})-", agenda_item_id)
    if not match:
        return None
    return date.fromisoformat(match.group(1))


def _source_key(source_ref: dict[str, Any]) -> str:
    source_type = str(source_ref.get("type") or "")
    source_id = str(
        source_ref.get("id")
        or source_ref.get("topic_id")
        or source_ref.get("path")
        or source_ref.get("course_code")
        or ""
    )
    return f"{source_type}:{source_id}"


async def _source_course_code(
    source_ref: dict[str, Any],
    fallback: str | None = None,
) -> str | None:
    explicit_course = source_ref.get("course_code")
    if isinstance(explicit_course, str) and explicit_course:
        return explicit_course

    source_type = source_ref.get("type")
    if source_type == "fall_behind":
        course_code = source_ref.get("course_code")
        return course_code if isinstance(course_code, str) else fallback
    if source_type == "course_files":
        return "IE"
    if source_type == "agenda_synthesis":
        source_id = source_ref.get("id")
        if isinstance(source_id, str) and source_id.startswith("timed-"):
            return source_id.removeprefix("timed-")
        return fallback
    if source_type == "exam":
        exam_id = source_ref.get("id")
        return exam_id if isinstance(exam_id, str) else fallback

    source_id = source_ref.get("id")
    if not isinstance(source_id, str):
        return fallback

    if source_type == "study_topic":
        for topic in await topics_svc.list_study_topics():
            if topic.id == source_id:
                return topic.course_code
    if source_type == "task":
        for task in await tasks_svc.list_tasks():
            if task.id == source_id:
                return task.course_code
    if source_type == "deliverable":
        for deliverable in await deliverables_svc.list_deliverables():
            if deliverable.id == source_id:
                return deliverable.course_code
    return fallback


def _open_tasks(tasks: list[Task]) -> list[Task]:
    return [task for task in tasks if task.status in {"open", "in_progress", "blocked"}]


def _open_deliverables(deliverables: list[Deliverable]) -> list[Deliverable]:
    return [
        deliverable
        for deliverable in deliverables
        if deliverable.status in {"open", "in_progress"}
    ]


def _priority_weight(value: str | None) -> int:
    return {
        "urgent": 12,
        "high": 8,
        "med": 4,
        "low": 0,
    }.get(value or "med", 4)


def _urgent_work_candidate(
    *,
    target_date: date,
    tasks: list[Task],
    deliverables: list[Deliverable],
) -> _Candidate | None:
    cutoff = _end_of_day(target_date)
    urgent_tasks = [
        task
        for task in _open_tasks(tasks)
        if task.due_at is not None and _as_utc(task.due_at) <= cutoff
    ]
    urgent_deliverables = [
        deliverable
        for deliverable in _open_deliverables(deliverables)
        if _as_utc(deliverable.due_at) <= cutoff
    ]

    choices: list[tuple[int, str, Task | Deliverable]] = []
    for task in urgent_tasks:
        if task.due_at is None:
            continue
        days_overdue = max(0, (target_date - _as_utc(task.due_at).date()).days)
        choices.append((92 + _priority_weight(task.priority) + days_overdue, "task", task))
    for deliverable in urgent_deliverables:
        days_overdue = max(0, (target_date - _as_utc(deliverable.due_at).date()).days)
        choices.append((96 + days_overdue, "deliverable", deliverable))
    if not choices:
        return None

    score, source_type, source = sorted(
        choices,
        key=lambda value: (
            -value[0],
            getattr(value[2], "due_at").isoformat(),
            getattr(value[2], "title", getattr(value[2], "name", "")),
        ),
    )[0]
    if isinstance(source, Task):
        if source.due_at is None:
            return None
        due_at = _as_utc(source.due_at)
        title = source.title
    else:
        due_at = _as_utc(source.due_at)
        title = source.name
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="urgent_work",
            title=title,
            course_code=source.course_code,
            reason=f"{source_type.title()} is due by {due_at.date().isoformat()} and is still open.",
            estimated_minutes=35 if source_type == "task" else 50,
            priority=score,
            source_ref={
                "type": source_type,
                "id": source.id,
                "due_at": due_at.isoformat(),
            },
        ),
        category_order=0,
    )


def _struggling_candidate(
    *,
    target_date: date,
    topics: list[StudyTopic],
) -> _Candidate | None:
    struggling = [
        topic
        for topic in topics
        if topic.status == "struggling" or (topic.confidence is not None and topic.confidence <= 1)
    ]
    if not struggling:
        return None
    topic = sorted(
        struggling,
        key=lambda item: (
            item.course_code,
            item.sort_order,
            item.name.lower(),
            item.id,
        ),
    )[0]
    confidence = topic.confidence if topic.confidence is not None else 1
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="struggling_topic",
            title=f"Retry {topic.name}",
            course_code=topic.course_code,
            reason=f"Marked struggling with confidence {confidence}; retry before adding more surface area.",
            estimated_minutes=35,
            priority=88 + max(0, 2 - confidence),
            source_ref={"type": "study_topic", "id": topic.id, "status": topic.status},
        ),
        category_order=1,
    )


def _review_candidate(
    *,
    target_date: date,
    fall_behind: list[FallBehindItem],
    topics: list[StudyTopic],
) -> _Candidate | None:
    warnings = [
        item
        for item in fall_behind
        if item.severity in {"warn", "critical"} and item.topics
    ]
    if warnings:
        warning = sorted(
            warnings,
            key=lambda item: (
                0 if item.severity == "critical" else 1,
                item.course_code,
            ),
        )[0]
        topic = sorted(
            warning.topics,
            key=lambda item: (
                item.covered_on or date.min,
                item.sort_order,
                item.name.lower(),
            ),
        )[0]
        priority = 88 if warning.severity == "critical" else 80
        return _Candidate(
            item=_agenda_item(
                target_date=target_date,
                kind="review",
                title=f"Review {topic.name}",
                course_code=topic.course_code,
                reason=(
                    f"Fall-behind {warning.severity} warning: this topic is still "
                    "unfinished before upcoming course work."
                ),
                estimated_minutes=30,
                priority=priority,
                source_ref={
                    "type": "fall_behind",
                    "course_code": warning.course_code,
                    "topic_id": topic.id,
                },
            ),
            category_order=2,
        )

    studied = [topic for topic in topics if topic.status in {"studied", "mastered"}]
    if not studied:
        return None
    topic = sorted(
        studied,
        key=lambda item: (
            item.last_reviewed_at or datetime.min.replace(tzinfo=timezone.utc),
            item.course_code,
            item.sort_order,
            item.name.lower(),
        ),
    )[0]
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="review",
            title=f"Spaced review: {topic.name}",
            course_code=topic.course_code,
            reason="Previously studied topic is ready for a spaced review pass.",
            estimated_minutes=20,
            priority=62,
            source_ref={"type": "study_topic", "id": topic.id, "status": topic.status},
        ),
        category_order=2,
    )


def _new_concept_candidate(
    *,
    target_date: date,
    topics: list[StudyTopic],
) -> _Candidate | None:
    candidates = [topic for topic in topics if topic.status == "not_started"]
    if not candidates:
        return None
    topic = sorted(
        candidates,
        key=lambda item: (
            item.course_code,
            item.sort_order,
            item.covered_on or date.max,
            item.name.lower(),
            item.id,
        ),
    )[0]
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="new_concept",
            title=f"Learn {topic.name}",
            course_code=topic.course_code,
            reason="Next not-started concept in the course sequence.",
            estimated_minutes=45,
            priority=55,
            source_ref={"type": "study_topic", "id": topic.id, "status": topic.status},
        ),
        category_order=3,
    )


def _timed_exercise_candidate(
    *,
    target_date: date,
    courses: list[Course],
    exams: list[Exam],
    topics: list[StudyTopic],
) -> _Candidate | None:
    scheduled = [
        exam
        for exam in exams
        if exam.status != "done" and exam.scheduled_at is not None
    ]
    if scheduled:
        exam = sorted(
            scheduled,
            key=lambda item: (
                _as_utc(item.scheduled_at)
                if item.scheduled_at
                else datetime.max.replace(tzinfo=timezone.utc),
                item.course_code,
            ),
        )[0]
        return _Candidate(
            item=_agenda_item(
                target_date=target_date,
                kind="timed_exercise",
                title=f"Timed exam drill for {exam.course_code}",
                course_code=exam.course_code,
                reason="Upcoming exam is scheduled; add timed retrieval practice.",
                estimated_minutes=40,
                priority=70,
                source_ref={"type": "exam", "id": exam.course_code},
            ),
            category_order=4,
        )

    course_codes = {topic.course_code for topic in topics} or {course.code for course in courses}
    if not course_codes:
        return None
    course_code = sorted(course_codes)[0]
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="timed_exercise",
            title=f"Timed practice block for {course_code}",
            course_code=course_code,
            reason="Timed execution keeps interview and exam practice honest.",
            estimated_minutes=30,
            priority=48,
            source_ref={"type": "agenda_synthesis", "id": f"timed-{course_code}"},
        ),
        category_order=4,
    )


async def _flashcard_candidate(target_date: date, course_code: str | None) -> _Candidate | None:
    if course_code and course_code not in {"IE", "INTENG"}:
        return None
    entries = await storage_svc.list_files(FLASHCARD_PREFIX, limit=100)
    files = [entry for entry in entries if entry.get("id") is not None]
    if not files:
        return None
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="flashcards",
            title="Review Interview Engineering flashcards",
            course_code="IE",
            reason=f"{len(files)} flashcard asset(s) are visible in the course files browser.",
            estimated_minutes=20,
            priority=58,
            source_ref={"type": "course_files", "path": FLASHCARD_PREFIX},
        ),
        category_order=5,
    )


async def _suppressed_source_keys(
    *,
    target_date: date,
    course_code: str | None,
    now: datetime,
) -> set[str]:
    suppressed: set[str] = set()
    skipped = await events_svc.list_events(
        kind=ACTION_EVENT_KIND["skipped"],
        course_code=course_code,
        limit=200,
    )
    snoozed = await events_svc.list_events(
        kind=ACTION_EVENT_KIND["snoozed"],
        course_code=course_code,
        limit=200,
    )
    for event in skipped:
        payload = event.payload or {}
        if payload.get("agenda_date") != target_date.isoformat():
            continue
        source_ref = payload.get("source_ref")
        if isinstance(source_ref, dict):
            suppressed.add(_source_key(source_ref))
    for event in snoozed:
        payload = event.payload or {}
        source_ref = payload.get("source_ref")
        snooze_until_raw = payload.get("snooze_until")
        if not isinstance(source_ref, dict) or not isinstance(snooze_until_raw, str):
            continue
        try:
            snooze_until = _as_utc(datetime.fromisoformat(snooze_until_raw))
        except ValueError:
            continue
        if snooze_until > _as_utc(now):
            suppressed.add(_source_key(source_ref))
    return suppressed


def _filter_suppressed(
    candidates: list[_Candidate],
    suppressed_source_keys: set[str],
) -> list[_Candidate]:
    return [
        candidate
        for candidate in candidates
        if _source_key(candidate.item.source_ref) not in suppressed_source_keys
    ]


def _planning_items(target_date: date, course_code: str | None) -> list[_Candidate]:
    labels = [
        ("Add a course or import curriculum", "Create the first course so OpenStudy can build a real agenda."),
        ("Add one study topic", "A topic backlog lets the agenda choose new concepts and reviews."),
        ("Add one dated task", "Due dates let the agenda surface urgent work before routine study."),
        ("Upload or sync course files", "Visible files give the agenda concrete resources to reference."),
    ]
    return [
        _Candidate(
            item=_agenda_item(
                target_date=target_date,
                kind="planning",
                title=title,
                course_code=course_code,
                reason=reason,
                estimated_minutes=15,
                priority=40 - index,
                source_ref={"type": "empty_state", "id": f"planning-{index + 1}"},
            ),
            category_order=10 + index,
        )
        for index, (title, reason) in enumerate(labels)
    ]


def _best_by_category(candidates: list[_Candidate]) -> list[_Candidate]:
    best: dict[int, _Candidate] = {}
    for candidate in candidates:
        current = best.get(candidate.category_order)
        if current is None or (
            candidate.item.priority,
            candidate.item.title.lower(),
            candidate.item.id,
        ) > (
            current.item.priority,
            current.item.title.lower(),
            current.item.id,
        ):
            best[candidate.category_order] = candidate
    return [best[key] for key in sorted(best)]


async def generate_daily_agenda(
    *,
    target_date: date | None = None,
    course_code: str | None = None,
    now: datetime | None = None,
) -> DailyAgenda:
    local_now = await _local_now()
    target_date = target_date or local_now.date()
    normalized_course = course_code.upper() if course_code else None
    now = now or datetime.combine(target_date, time(12, 0), tzinfo=timezone.utc)

    courses = await courses_svc.list_courses()
    slots = await slots_svc.list_slots(course_code=normalized_course)
    exams = _course_filter(await exams_svc.list_exams(), normalized_course)
    topics = await topics_svc.list_study_topics(course_code=normalized_course)
    deliverables = await deliverables_svc.list_deliverables(course_code=normalized_course)
    tasks = await tasks_svc.list_tasks(course_code=normalized_course)
    await lectures_svc.list_lectures(course_code=normalized_course)
    await events_svc.list_events(course_code=normalized_course, limit=50)

    filtered_courses = (
        [course for course in courses if course.code == normalized_course]
        if normalized_course
        else courses
    )
    course_names = _course_name_by_code(filtered_courses)
    fall_behind = fall_behind_svc.compute_fall_behind(
        filtered_courses,
        topics,
        slots,
        now,
    )

    candidates: list[_Candidate] = []
    for candidate in (
        _urgent_work_candidate(
            target_date=target_date,
            tasks=tasks,
            deliverables=deliverables,
        ),
        _struggling_candidate(target_date=target_date, topics=topics),
        _review_candidate(
            target_date=target_date,
            fall_behind=fall_behind,
            topics=topics,
        ),
        _new_concept_candidate(target_date=target_date, topics=topics),
        _timed_exercise_candidate(
            target_date=target_date,
            courses=filtered_courses,
            exams=exams,
            topics=topics,
        ),
        await _flashcard_candidate(target_date, normalized_course),
    ):
        if candidate is not None:
            candidates.append(candidate)

    suppressed = await _suppressed_source_keys(
        target_date=target_date,
        course_code=normalized_course,
        now=now,
    )
    candidates = _filter_suppressed(candidates, suppressed)

    if not candidates:
        candidates = _planning_items(target_date, normalized_course)

    selected = _best_by_category(candidates)[:AGENDA_MAX_ITEMS]
    items = [candidate.item for candidate in selected]
    for item in items:
        if item.course_code and item.course_code in course_names:
            item.reason = f"{item.reason} Course: {course_names[item.course_code]}."
    return DailyAgenda(date=target_date, course_code=normalized_course, items=items)


async def resolve_agenda_item(
    agenda_item_id: str,
    source_ref: dict[str, Any] | None = None,
    *,
    course_code: str | None = None,
) -> AgendaItem:
    if source_ref is not None:
        item_date = parse_agenda_item_date(agenda_item_id) or (await _local_now()).date()
        return _agenda_item(
            target_date=item_date,
            kind=agenda_item_id.split("-", 4)[4].rsplit("-", 1)[0]
            if agenda_item_id.startswith("agenda-")
            else "unknown",
            title=agenda_item_id,
            course_code=course_code,
            reason="Resolved from client-supplied source reference.",
            estimated_minutes=0,
            priority=1,
            source_ref=source_ref,
        )

    item_date = parse_agenda_item_date(agenda_item_id)
    agenda = await generate_daily_agenda(target_date=item_date, course_code=course_code)
    for item in agenda.items:
        if item.id == agenda_item_id:
            return item
    raise ValueError(f"agenda item {agenda_item_id!r} could not be resolved")


def _request_payload(request: AgendaActionRequest | AgendaResultRequest) -> dict[str, Any]:
    return request.model_dump(mode="json", exclude_none=True)


async def _record_action_event(
    *,
    agenda_item_id: str,
    outcome: str,
    source_ref: dict[str, Any],
    course_code: str | None,
    request: AgendaActionRequest | AgendaResultRequest,
    mutations_applied: list[str],
    target_date: date | None,
) -> str:
    payload = {
        "agenda_item_id": agenda_item_id,
        "agenda_date": (target_date or parse_agenda_item_date(agenda_item_id) or (await _local_now()).date()).isoformat(),
        "outcome": outcome,
        "source_ref": source_ref,
        "mutations_applied": mutations_applied,
        **_request_payload(request),
    }
    event = await events_svc.record_event(
        EventCreate(
            kind=ACTION_EVENT_KIND.get(outcome, "agenda:result"),
            course_code=course_code,
            payload=payload,
        )
    )
    return event.id


async def apply_agenda_result_to_source(
    *,
    agenda_item_id: str,
    item_kind: str,
    source_ref: dict[str, Any],
    request: AgendaActionRequest | AgendaResultRequest,
    outcome: str,
) -> list[str]:
    if outcome != "completed":
        return []

    source_type = source_ref.get("type")
    mutations: list[str] = []
    if source_type == "study_topic":
        topic_id = source_ref.get("id")
        if isinstance(topic_id, str):
            await topics_svc.update_study_topic(
                topic_id,
                StudyTopicPatch(status="studied", confidence=request.confidence),
            )
            mutations.append("study_topic:studied")
            if request.confidence is not None:
                mutations.append("study_topic:confidence")
    elif source_type == "fall_behind":
        topic_id = source_ref.get("topic_id")
        if isinstance(topic_id, str):
            await topics_svc.update_study_topic(
                topic_id,
                StudyTopicPatch(status="studied", confidence=request.confidence),
            )
            mutations.append("study_topic:studied")
            if request.confidence is not None:
                mutations.append("study_topic:confidence")
    elif source_type == "task":
        task_id = source_ref.get("id")
        if isinstance(task_id, str):
            await tasks_svc.complete_task(task_id)
            mutations.append("task:done")
    elif source_type in {"deliverable", "course_files", "agenda_synthesis", "exam"}:
        return []
    else:
        raise ValueError(f"unsupported agenda source type for {agenda_item_id}: {source_type!r}")

    return mutations


def _snooze_until(request: AgendaActionRequest, now: datetime | None = None) -> datetime:
    if request.snooze_until is not None:
        return _as_utc(request.snooze_until)
    minutes = request.snooze_minutes or 60
    base = _as_utc(now or datetime.now(timezone.utc))
    return base + timedelta(minutes=minutes)


async def _action_response(
    *,
    agenda_item_id: str,
    outcome: str,
    request: AgendaActionRequest | AgendaResultRequest,
    source_ref: dict[str, Any],
    course_code: str | None,
    mutations_applied: list[str],
    event_id: str,
    message: str,
    include_agenda: bool,
    target_date: date | None,
) -> AgendaActionResponse:
    refreshed = (
        await generate_daily_agenda(target_date=target_date, course_code=course_code)
        if include_agenda
        else None
    )
    return AgendaActionResponse(
        agenda_item_id=agenda_item_id,
        outcome=outcome,  # type: ignore[arg-type]
        source_ref=source_ref,
        mutations_applied=mutations_applied,
        event_id=event_id,
        message=message,
        refresh_recommended=True,
        agenda=refreshed,
    )


async def complete_agenda_item(
    agenda_item_id: str,
    request: AgendaActionRequest | None = None,
    *,
    include_agenda: bool = False,
) -> AgendaActionResponse:
    request = request or AgendaActionRequest()
    item = await resolve_agenda_item(agenda_item_id, request.source_ref)
    source_ref = request.source_ref or item.source_ref
    target_date = parse_agenda_item_date(agenda_item_id)
    course_code = await _source_course_code(source_ref, item.course_code)
    mutations = await apply_agenda_result_to_source(
        agenda_item_id=agenda_item_id,
        item_kind=item.kind,
        source_ref=source_ref,
        request=request,
        outcome="completed",
    )
    event_id = await _record_action_event(
        agenda_item_id=agenda_item_id,
        outcome="completed",
        source_ref=source_ref,
        course_code=course_code,
        request=request,
        mutations_applied=mutations,
        target_date=target_date,
    )
    return await _action_response(
        agenda_item_id=agenda_item_id,
        outcome="completed",
        request=request,
        source_ref=source_ref,
        course_code=course_code,
        mutations_applied=mutations,
        event_id=event_id,
        message="Agenda item completed; refresh the agenda to see the next best action.",
        include_agenda=include_agenda,
        target_date=target_date,
    )


async def skip_agenda_item(
    agenda_item_id: str,
    request: AgendaActionRequest | None = None,
    *,
    include_agenda: bool = False,
) -> AgendaActionResponse:
    request = request or AgendaActionRequest()
    item = await resolve_agenda_item(agenda_item_id, request.source_ref)
    source_ref = request.source_ref or item.source_ref
    target_date = parse_agenda_item_date(agenda_item_id)
    course_code = await _source_course_code(source_ref, item.course_code)
    event_id = await _record_action_event(
        agenda_item_id=agenda_item_id,
        outcome="skipped",
        source_ref=source_ref,
        course_code=course_code,
        request=request,
        mutations_applied=[],
        target_date=target_date,
    )
    return await _action_response(
        agenda_item_id=agenda_item_id,
        outcome="skipped",
        request=request,
        source_ref=source_ref,
        course_code=course_code,
        mutations_applied=[],
        event_id=event_id,
        message="Agenda item skipped for this agenda date.",
        include_agenda=include_agenda,
        target_date=target_date,
    )


async def snooze_agenda_item(
    agenda_item_id: str,
    request: AgendaActionRequest | None = None,
    *,
    include_agenda: bool = False,
    now: datetime | None = None,
) -> AgendaActionResponse:
    request = request or AgendaActionRequest()
    snooze_until = _snooze_until(request, now=now)
    request = request.model_copy(update={"snooze_until": snooze_until})
    item = await resolve_agenda_item(agenda_item_id, request.source_ref)
    source_ref = request.source_ref or item.source_ref
    target_date = parse_agenda_item_date(agenda_item_id)
    course_code = await _source_course_code(source_ref, item.course_code)
    event_id = await _record_action_event(
        agenda_item_id=agenda_item_id,
        outcome="snoozed",
        source_ref=source_ref,
        course_code=course_code,
        request=request,
        mutations_applied=[],
        target_date=target_date,
    )
    return await _action_response(
        agenda_item_id=agenda_item_id,
        outcome="snoozed",
        request=request,
        source_ref=source_ref,
        course_code=course_code,
        mutations_applied=[],
        event_id=event_id,
        message=f"Agenda item snoozed until {snooze_until.isoformat()}.",
        include_agenda=include_agenda,
        target_date=target_date,
    )


async def log_agenda_result(
    agenda_item_id: str,
    request: AgendaResultRequest,
    *,
    include_agenda: bool = False,
) -> AgendaActionResponse:
    item = await resolve_agenda_item(agenda_item_id, request.source_ref)
    source_ref = request.source_ref or item.source_ref
    target_date = parse_agenda_item_date(agenda_item_id)
    course_code = await _source_course_code(source_ref, item.course_code)
    mutations = await apply_agenda_result_to_source(
        agenda_item_id=agenda_item_id,
        item_kind=item.kind,
        source_ref=source_ref,
        request=request,
        outcome=request.outcome,
    )
    event_id = await _record_action_event(
        agenda_item_id=agenda_item_id,
        outcome=request.outcome,
        source_ref=source_ref,
        course_code=course_code,
        request=request,
        mutations_applied=mutations,
        target_date=target_date,
    )
    return await _action_response(
        agenda_item_id=agenda_item_id,
        outcome=request.outcome,
        request=request,
        source_ref=source_ref,
        course_code=course_code,
        mutations_applied=mutations,
        event_id=event_id,
        message=f"Agenda result logged as {request.outcome}.",
        include_agenda=include_agenda,
        target_date=target_date,
    )


__all__ = [
    "generate_daily_agenda",
    "parse_agenda_item_date",
    "resolve_agenda_item",
    "complete_agenda_item",
    "skip_agenda_item",
    "snooze_agenda_item",
    "log_agenda_result",
    "apply_agenda_result_to_source",
]
