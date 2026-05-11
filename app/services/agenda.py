"""Adaptive daily agenda generation.

The agenda engine is intentionally explainable: it gathers learner state,
builds candidate actions from retry/review/mastery evidence, and returns the
highest value 4-6 items in a stable category order. It does not call an LLM.
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
    TaskPatch,
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
    duration_range: tuple[int, int] | None = None,
    objective: str | None = None,
    mastery_phase: str | None = None,
    source_label: str | None = None,
    completion_criteria: str | None = None,
    confidence_target: int | None = None,
    retry_behavior: str | None = None,
    priority: int,
    source_ref: dict[str, Any],
) -> AgendaItem:
    source_key = str(source_ref.get("id") or source_ref.get("path") or title)
    min_minutes, max_minutes = duration_range or (estimated_minutes, estimated_minutes)
    return AgendaItem(
        id=f"agenda-{target_date.isoformat()}-{kind}-{_slug(source_key)}",
        kind=kind,
        title=title,
        course_code=course_code,
        reason=reason,
        estimated_minutes=estimated_minutes,
        duration_min_minutes=min_minutes,
        duration_max_minutes=max_minutes,
        objective=objective or title,
        mastery_phase=mastery_phase,  # type: ignore[arg-type]
        source_label=source_label,
        completion_criteria=completion_criteria or "Complete the selected agenda item.",
        confidence_target=confidence_target,
        retry_behavior=retry_behavior or "If failed or low confidence, log the result so the agenda can retry it.",
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


def _due_for_review(value: datetime | None, target_date: date) -> bool:
    if value is None:
        return False
    return _as_utc(value).date() <= target_date


def _mastery_state(item: StudyTopic | Task) -> str:
    return getattr(item, "mastery_state", None) or getattr(item, "status", None) or "not_started"


def _confidence(item: StudyTopic | Task) -> int | None:
    return getattr(item, "confidence", None) or getattr(item, "last_confidence", None)


def _low_confidence(item: StudyTopic | Task) -> bool:
    confidence = _confidence(item)
    return confidence is not None and confidence <= 1


def _duration_for_phase(phase: str, target_date: date) -> tuple[int, int]:
    if phase in {"retry_stabilization", "retention_verification"}:
        return (45, 90)
    if phase in {"timed_execution", "independent_practice"}:
        return (90, 150)
    if target_date.isoweekday() == 6:
        return (180, 300)
    return (45, 90)


def _source_ref_for_item(item: StudyTopic | Task) -> dict[str, Any]:
    if isinstance(item, StudyTopic):
        return {
            "type": "study_topic",
            "id": item.id,
            "status": item.status,
            "mastery_state": _mastery_state(item),
        }
    return {
        "type": "task",
        "id": item.id,
        "status": item.status,
        "mastery_state": _mastery_state(item),
    }


def _mastery_patch_for_result(
    *,
    item_kind: str,
    confidence: int | None,
    error_count: int | None,
    prior_retry_count: int,
    target_date: date | None,
) -> tuple[str, str, datetime | None, int, int]:
    now = datetime.now(timezone.utc)
    confidence_value = confidence if confidence is not None else 3
    error_value = error_count if error_count is not None else 0
    base_date = target_date or now.date()
    if confidence_value <= 1:
        return (
            "struggling",
            "struggling",
            datetime.combine(base_date + timedelta(days=1), time(9), tzinfo=timezone.utc),
            prior_retry_count + 1,
            9,
        )
    if confidence_value == 2:
        return (
            "in_progress",
            "guided_practice",
            datetime.combine(base_date + timedelta(days=1), time(9), tzinfo=timezone.utc),
            prior_retry_count + 1,
            6,
        )
    if confidence_value == 3:
        return (
            "in_progress",
            "independent_practice",
            datetime.combine(base_date + timedelta(days=2), time(9), tzinfo=timezone.utc),
            prior_retry_count + int(error_value > 0),
            4 if error_value > 0 else 0,
        )
    if confidence_value == 4:
        return (
            "studied",
            "timed_execution",
            datetime.combine(base_date + timedelta(days=3), time(9), tzinfo=timezone.utc),
            prior_retry_count,
            0,
        )
    if item_kind == "retention_check" and error_value <= 1:
        return ("mastered", "mastered", None, prior_retry_count, 0)
    if error_value <= 1:
        return (
            "studied",
            "retention_verification",
            datetime.combine(base_date + timedelta(days=3), time(9), tzinfo=timezone.utc),
            prior_retry_count,
            0,
        )
    return (
        "in_progress",
        "retry_stabilization",
        datetime.combine(base_date + timedelta(days=1), time(9), tzinfo=timezone.utc),
        prior_retry_count + 1,
        7,
    )


def _retry_candidate(
    *,
    target_date: date,
    topics: list[StudyTopic],
    tasks: list[Task],
) -> _Candidate | None:
    candidates: list[StudyTopic | Task] = [
        item
        for item in [*topics, *tasks]
        if (
            _mastery_state(item) == "retry_stabilization"
            or getattr(item, "retry_priority", 0) > 0
            or getattr(item, "retry_count", 0) > 0
        )
        and (
            _due_for_review(getattr(item, "next_review_at", None), target_date)
            or getattr(item, "retry_priority", 0) > 0
        )
        and getattr(item, "status", None) not in {"done", "skipped", "mastered"}
    ]
    if not candidates:
        return None
    item = sorted(
        candidates,
        key=lambda value: (
            -getattr(value, "retry_priority", 0),
            getattr(value, "next_review_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            -getattr(value, "error_count", 0),
            getattr(value, "sort_order", 0),
            getattr(value, "name", getattr(value, "title", "")).lower(),
        ),
    )[0]
    phase = _mastery_state(item)
    min_minutes, max_minutes = _duration_for_phase(phase, target_date)
    title_value = getattr(item, "name", getattr(item, "title", "item"))
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="retry",
            title=f"Retry {title_value}",
            course_code=item.course_code,
            reason=(
                "Overdue retry work is first-class: previous attempts still have "
                f"{getattr(item, 'error_count', 0)} error(s) or pending stabilization."
            ),
            estimated_minutes=min_minutes,
            duration_range=(min_minutes, max_minutes),
            objective=f"Stabilize {title_value} before introducing unrelated new material.",
            mastery_phase=phase,
            source_label=title_value,
            completion_criteria="Complete the retry, record errors, and reach the confidence target.",
            confidence_target=4,
            retry_behavior="If confidence stays below 4 or errors remain, keep it in retry stabilization.",
            priority=98 + getattr(item, "retry_priority", 0),
            source_ref=_source_ref_for_item(item),
        ),
        category_order=1,
    )


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
    tasks: list[Task] | None = None,
) -> _Candidate | None:
    task_items = tasks or []
    struggling: list[StudyTopic | Task] = [
        topic
        for topic in topics
        if topic.status == "struggling"
        or _mastery_state(topic) == "struggling"
        or _low_confidence(topic)
    ]
    struggling.extend(
        task
        for task in task_items
        if task.status != "done"
        and (
            _mastery_state(task) == "struggling"
            or _low_confidence(task)
        )
    )
    if not struggling:
        return None
    item = sorted(
        struggling,
        key=lambda item: (
            item.course_code,
            getattr(item, "sort_order", 0),
            getattr(item, "name", getattr(item, "title", "")).lower(),
            item.id,
        ),
    )[0]
    title_value = getattr(item, "name", getattr(item, "title", "item"))
    confidence = _confidence(item)
    confidence = confidence if confidence is not None else 1
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="struggling_topic",
            title=f"Retry {title_value}",
            course_code=item.course_code,
            reason=f"Marked struggling with confidence {confidence}; retry before adding more surface area.",
            estimated_minutes=35,
            duration_range=(45, 90),
            objective=f"Rebuild {title_value} with guided examples and error analysis.",
            mastery_phase="struggling",
            source_label=title_value,
            completion_criteria="Identify the failure reason, complete one guided retry, and log confidence.",
            confidence_target=3,
            retry_behavior="If confidence is 0-1, keep it struggling; if 2, move to guided practice.",
            priority=88 + max(0, 2 - confidence),
            source_ref=_source_ref_for_item(item),
        ),
        category_order=2,
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
                duration_range=(45, 90),
                objective=f"Recover unfinished understanding for {topic.name}.",
                mastery_phase=_mastery_state(topic),
                source_label=topic.name,
                completion_criteria="Review the topic and record the next confidence score.",
                confidence_target=3,
                retry_behavior="If errors remain, schedule retry stabilization instead of new exposure.",
                priority=priority,
                source_ref={
                    "type": "fall_behind",
                    "course_code": warning.course_code,
                    "topic_id": topic.id,
                },
            ),
            category_order=3,
        )

    due_retention = [
        topic
        for topic in topics
        if _mastery_state(topic) == "retention_verification"
        and _due_for_review(topic.next_review_at, target_date)
    ]
    if due_retention:
        topic = sorted(
            due_retention,
            key=lambda item: (
                item.next_review_at or datetime.min.replace(tzinfo=timezone.utc),
                item.course_code,
                item.sort_order,
                item.name.lower(),
            ),
        )[0]
        return _Candidate(
            item=_agenda_item(
                target_date=target_date,
                kind="retention_check",
                title=f"Retention check: {topic.name}",
                course_code=topic.course_code,
                reason="Delayed retention check is due before this unit can be mastered.",
                estimated_minutes=45,
                duration_range=(45, 90),
                objective=f"Verify {topic.name} after a delay without relying on fresh memory.",
                mastery_phase="retention_verification",
                source_label=topic.name,
                completion_criteria="Pass delayed retention with confidence 5 and low error count.",
                confidence_target=5,
                retry_behavior="If failed, move the unit back to retry stabilization with a new review date.",
                priority=86,
                source_ref=_source_ref_for_item(topic),
            ),
            category_order=3,
        )

    studied = [
        topic
        for topic in topics
        if topic.status in {"studied", "mastered"}
        or _mastery_state(topic) == "mastered"
    ]
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
            duration_range=(45, 90),
            objective=f"Refresh {topic.name} before decay becomes visible.",
            mastery_phase=_mastery_state(topic),
            source_label=topic.name,
            completion_criteria="Complete a spaced review pass and update confidence.",
            confidence_target=4,
            retry_behavior="If confidence drops below 4, schedule guided or independent practice.",
            priority=62,
            source_ref={"type": "study_topic", "id": topic.id, "status": topic.status},
        ),
        category_order=3,
    )


def _mastery_progression_candidate(
    *,
    target_date: date,
    topics: list[StudyTopic],
) -> _Candidate | None:
    candidates = [
        topic
        for topic in topics
        if _mastery_state(topic)
        in {"exposure", "understanding", "guided_practice", "independent_practice", "timed_execution"}
        and topic.status not in {"mastered", "struggling"}
    ]
    if not candidates:
        return None
    topic = sorted(
        candidates,
        key=lambda item: (
            -(_confidence(item) or 0),
            item.sort_order,
            item.name.lower(),
            item.id,
        ),
    )[0]
    phase = _mastery_state(topic)
    min_minutes, max_minutes = _duration_for_phase(phase, target_date)
    return _Candidate(
        item=_agenda_item(
            target_date=target_date,
            kind="mastery_progression",
            title=f"Advance {topic.name}",
            course_code=topic.course_code,
            reason=f"Active mastery unit is in {phase}; advance it before adding new exposure.",
            estimated_minutes=min_minutes,
            duration_range=(min_minutes, max_minutes),
            objective=f"Move {topic.name} to the next mastery phase using current evidence.",
            mastery_phase=phase,
            source_label=topic.name,
            completion_criteria="Meet the confidence gate and log timing/errors for the next phase decision.",
            confidence_target=4 if phase in {"independent_practice", "timed_execution"} else 3,
            retry_behavior="If confidence or timing is weak, keep the unit active and retry before new lessons.",
            priority=76,
            source_ref=_source_ref_for_item(topic),
        ),
        category_order=4,
    )


def _new_concept_candidate(
    *,
    target_date: date,
    topics: list[StudyTopic],
    tasks: list[Task] | None = None,
) -> _Candidate | None:
    task_candidates = [
        task
        for task in (tasks or [])
        if task.status == "open"
        and _mastery_state(task) in {"not_started", "exposure"}
        and task.tags
        and "curriculum" in task.tags
    ]
    if task_candidates:
        task = sorted(
            task_candidates,
            key=lambda item: (
                item.course_code or "",
                item.title.lower(),
                item.id,
            ),
        )[0]
        return _Candidate(
            item=_agenda_item(
                target_date=target_date,
                kind="new_exposure",
                title=f"Expose: {task.title}",
                course_code=task.course_code,
                reason="New exposure is allowed because higher-priority retry, struggling, review, and active unit work have capacity.",
                estimated_minutes=45,
                duration_range=(45, 90),
                objective=f"Get first exposure to {task.title} without marking it complete prematurely.",
                mastery_phase="exposure",
                source_label=task.title,
                completion_criteria="Record whether the idea is only exposed or ready for guided practice.",
                confidence_target=2,
                retry_behavior="If confidence is 0-1, log it as struggling and schedule retry work.",
                priority=54,
                source_ref=_source_ref_for_item(task),
            ),
            category_order=6,
        )
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
            duration_range=(45, 90),
            objective=f"Start exposure for {topic.name}.",
            mastery_phase="exposure",
            source_label=topic.name,
            completion_criteria="Complete first exposure and record confidence; do not mark mastered.",
            confidence_target=2,
            retry_behavior="If confidence is 0-1, convert to struggling retry work.",
            priority=55,
            source_ref={"type": "study_topic", "id": topic.id, "status": topic.status},
        ),
        category_order=6,
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
                duration_range=(90, 150),
                objective=f"Practice timed execution for {exam.course_code}.",
                mastery_phase="timed_execution",
                source_label=exam.course_code,
                completion_criteria="Finish the timed drill and record completion count, errors, and confidence.",
                confidence_target=4,
                retry_behavior="If timing or errors miss the target, schedule retry stabilization.",
                priority=70,
                source_ref={"type": "exam", "id": exam.course_code},
            ),
            category_order=5,
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
            duration_range=_duration_for_phase("timed_execution", target_date),
            objective=f"Maintain timed interview execution for {course_code}.",
            mastery_phase="timed_execution",
            source_label=course_code,
            completion_criteria="Log duration, completed count, total count, errors, and confidence.",
            confidence_target=4,
            retry_behavior="If performance is weak, convert the weak unit into retry stabilization.",
            priority=48,
            source_ref={"type": "agenda_synthesis", "id": f"timed-{course_code}"},
        ),
        category_order=5,
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
            duration_range=(45, 90),
            objective="Refresh Interview Engineering memory assets.",
            mastery_phase="retention_verification",
            source_label="Interview Engineering flashcards",
            completion_criteria="Review active cards and log stale or failed prompts.",
            confidence_target=4,
            retry_behavior="Turn missed cards into retry work instead of optional review.",
            priority=58,
            source_ref={"type": "course_files", "path": FLASHCARD_PREFIX},
        ),
        category_order=7,
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
        _retry_candidate(target_date=target_date, topics=topics, tasks=tasks),
        _struggling_candidate(target_date=target_date, topics=topics, tasks=tasks),
        _review_candidate(
            target_date=target_date,
            fall_behind=fall_behind,
            topics=topics,
        ),
        _mastery_progression_candidate(target_date=target_date, topics=topics),
        _timed_exercise_candidate(
            target_date=target_date,
            courses=filtered_courses,
            exams=exams,
            topics=topics,
        ),
        _new_concept_candidate(target_date=target_date, topics=topics, tasks=tasks),
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
            topic = next(
                (item for item in await topics_svc.list_study_topics() if item.id == topic_id),
                None,
            )
            retry_count = topic.retry_count if topic is not None else 0
            status, mastery_state, next_review_at, next_retry_count, retry_priority = (
                _mastery_patch_for_result(
                    item_kind="retention_check"
                    if source_ref.get("mastery_state") == "retention_verification"
                    else item_kind,
                    confidence=request.confidence,
                    error_count=request.error_count,
                    prior_retry_count=retry_count,
                    target_date=parse_agenda_item_date(agenda_item_id),
                )
            )
            await topics_svc.update_study_topic(
                topic_id,
                StudyTopicPatch(
                    status=status,  # type: ignore[arg-type]
                    confidence=request.confidence,
                    mastery_state=mastery_state,  # type: ignore[arg-type]
                    retry_count=next_retry_count,
                    last_attempted_at=datetime.now(timezone.utc),
                    last_completed_at=datetime.now(timezone.utc),
                    last_reviewed_at=datetime.now(timezone.utc),
                    next_review_at=next_review_at,
                    last_confidence=request.confidence,
                    error_count=request.error_count or 0,
                    failure_reason=request.notes if (request.error_count or 0) > 0 else None,
                    retry_priority=retry_priority,
                ),
            )
            mutations.append("study_topic:mastery_state")
            if status in {"studied", "mastered"}:
                mutations.append(f"study_topic:{status}")
            if request.confidence is not None:
                mutations.append("study_topic:confidence")
    elif source_type == "fall_behind":
        topic_id = source_ref.get("topic_id")
        if isinstance(topic_id, str):
            topic = next(
                (item for item in await topics_svc.list_study_topics() if item.id == topic_id),
                None,
            )
            retry_count = topic.retry_count if topic is not None else 0
            status, mastery_state, next_review_at, next_retry_count, retry_priority = (
                _mastery_patch_for_result(
                    item_kind="retention_check"
                    if source_ref.get("mastery_state") == "retention_verification"
                    else item_kind,
                    confidence=request.confidence,
                    error_count=request.error_count,
                    prior_retry_count=retry_count,
                    target_date=parse_agenda_item_date(agenda_item_id),
                )
            )
            await topics_svc.update_study_topic(
                topic_id,
                StudyTopicPatch(
                    status=status,  # type: ignore[arg-type]
                    confidence=request.confidence,
                    mastery_state=mastery_state,  # type: ignore[arg-type]
                    retry_count=next_retry_count,
                    last_attempted_at=datetime.now(timezone.utc),
                    last_completed_at=datetime.now(timezone.utc),
                    last_reviewed_at=datetime.now(timezone.utc),
                    next_review_at=next_review_at,
                    last_confidence=request.confidence,
                    error_count=request.error_count or 0,
                    failure_reason=request.notes if (request.error_count or 0) > 0 else None,
                    retry_priority=retry_priority,
                ),
            )
            mutations.append("study_topic:mastery_state")
            if status in {"studied", "mastered"}:
                mutations.append(f"study_topic:{status}")
            if request.confidence is not None:
                mutations.append("study_topic:confidence")
    elif source_type == "task":
        task_id = source_ref.get("id")
        if isinstance(task_id, str):
            if source_ref.get("mastery_state") or item_kind in {"new_exposure", "retry"}:
                await tasks_svc.update_task(
                    task_id,
                    TaskPatch(
                        mastery_state=_mastery_patch_for_result(
                            item_kind=item_kind,
                            confidence=request.confidence,
                            error_count=request.error_count,
                            prior_retry_count=0,
                            target_date=parse_agenda_item_date(agenda_item_id),
                        )[1],  # type: ignore[arg-type]
                        last_attempted_at=datetime.now(timezone.utc),
                        last_completed_at=datetime.now(timezone.utc),
                        last_confidence=request.confidence,
                        error_count=request.error_count or 0,
                    ),
                )
                mutations.append("task:mastery_state")
            else:
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
