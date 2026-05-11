"""Seed a schema v2 curriculum manifest into the OpenStudy database.

Run from the repo root:
    python scripts/curriculum/seed_openstudy.py --verbose

The current OpenStudy schema has no dedicated curriculum/source/asset tables.
This script maps the curriculum into existing tables and stores source,
dependency, and asset details in JSON metadata fields.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app import db  # noqa: E402
from scripts.curriculum.validate_manifest import (  # noqa: E402
    ValidationOptions,
    load_manifest as load_manifest_for_validation,
)
from scripts.curriculum.validate_manifest import validate_manifest  # noqa: E402


DEFAULT_MANIFEST = Path("curriculum/interview_manifest.v2.yaml")
DEFAULT_COURSE_CODE = "IE"
MARKER_PREFIX = "OPENSTUDY_CURRICULUM_V2"


@dataclass
class ActionCounts:
    create: int = 0
    update: int = 0
    skip: int = 0
    delete: int = 0

    @property
    def total(self) -> int:
        return self.create + self.update + self.skip + self.delete


@dataclass
class SeedPlan:
    course_code: str
    manifest_id: str
    dry_run: bool
    courses: ActionCounts = field(default_factory=ActionCounts)
    sources: ActionCounts = field(default_factory=ActionCounts)
    assets: ActionCounts = field(default_factory=ActionCounts)
    tracks: ActionCounts = field(default_factory=ActionCounts)
    modules: ActionCounts = field(default_factory=ActionCounts)
    lessons: ActionCounts = field(default_factory=ActionCounts)
    dependencies: int = 0
    warnings: list[str] = field(default_factory=list)


def compact_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def marker(kind: str, stable_id: str) -> str:
    return f"{MARKER_PREFIX}:{kind}:{stable_id}"


def metadata_text(kind: str, stable_id: str, metadata: dict[str, Any]) -> str:
    return f"{marker(kind, stable_id)}\n{compact_json(metadata)}"


def stable_course_code(manifest: dict[str, Any], explicit: str | None = None) -> str:
    if explicit:
        return explicit
    meta = manifest.get("meta", {})
    if isinstance(meta, dict):
        course_code = meta.get("course_code")
        if isinstance(course_code, str) and course_code.strip():
            return course_code.strip()
    return DEFAULT_COURSE_CODE


def read_manifest(path: Path, *, allow_missing_source_files: bool = False) -> dict[str, Any]:
    data, exit_code = load_manifest_for_validation(path)
    if data is None:
        raise ValueError(f"could not load manifest {path} (exit {exit_code})")
    result = validate_manifest(
        data,
        path,
        ValidationOptions(
            allow_missing_source_files=(
                allow_missing_source_files
                or os.environ.get("OPENSTUDY_PACKAGED_CURRICULUM") == "1"
            )
        ),
    )
    if result.errors:
        messages = "; ".join(f"{issue.path}: {issue.message}" for issue in result.errors[:5])
        raise ValueError(
            f"manifest validation failed with {len(result.errors)} error(s): {messages}"
        )
    return data


def iter_tracks(manifest: dict[str, Any]) -> Iterable[dict[str, Any]]:
    tracks = manifest.get("tracks", [])
    return tracks if isinstance(tracks, list) else []


def iter_modules(manifest: dict[str, Any]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for track in iter_tracks(manifest):
        modules = track.get("modules", [])
        if isinstance(modules, list):
            for module in modules:
                if isinstance(module, dict):
                    yield track, module


def iter_lessons(
    manifest: dict[str, Any],
) -> Iterable[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    for track, module in iter_modules(manifest):
        lessons = module.get("lessons", [])
        if isinstance(lessons, list):
            for lesson in lessons:
                if isinstance(lesson, dict):
                    yield track, module, lesson


def dependency_count(manifest: dict[str, Any]) -> int:
    count = 0
    for track in iter_tracks(manifest):
        depends_on = track.get("depends_on", [])
        if isinstance(depends_on, list):
            count += len(depends_on)
    for _, module in iter_modules(manifest):
        for key in ("depends_on", "prerequisites"):
            refs = module.get(key, [])
            if isinstance(refs, list):
                count += len(refs)
    for _, _, lesson in iter_lessons(manifest):
        depends_on = lesson.get("depends_on", [])
        if isinstance(depends_on, list):
            count += len(depends_on)
    return count


def course_metadata(manifest: dict[str, Any], course_code: str) -> dict[str, Any]:
    meta = manifest.get("meta", {})
    tracks = [
        {
            "id": track.get("id"),
            "name": track.get("name"),
            "suggested_order": track.get("suggested_order"),
            "order_semantics": "weak_tiebreaker",
            "depends_on": track.get("depends_on", []),
        }
        for track in iter_tracks(manifest)
    ]
    return {
        "kind": "course",
        "manifest_id": meta.get("id") if isinstance(meta, dict) else None,
        "schema_version": meta.get("schema_version") if isinstance(meta, dict) else None,
        "course_code": course_code,
        "meta": meta,
        "adaptive_model": {
            "agenda_is_source_of_truth": True,
            "suggested_order_semantics": "weak_tiebreaker",
            "mastery_states": [
                "not_started",
                "exposure",
                "understanding",
                "guided_practice",
                "independent_practice",
                "timed_execution",
                "retry_stabilization",
                "retention_verification",
                "mastered",
                "struggling",
            ],
        },
        "tracks": tracks,
    }


def module_metadata(
    manifest: dict[str, Any], track: dict[str, Any], module: dict[str, Any]
) -> dict[str, Any]:
    meta = manifest.get("meta", {})
    return {
        "kind": "module",
        "manifest_id": meta.get("id") if isinstance(meta, dict) else None,
        "schema_version": meta.get("schema_version") if isinstance(meta, dict) else None,
        "track": {
            "id": track.get("id"),
            "name": track.get("name"),
            "suggested_order": track.get("suggested_order"),
            "order_semantics": "weak_tiebreaker",
            "depends_on": track.get("depends_on", []),
        },
        "module": {
            "id": module.get("id"),
            "name": module.get("name"),
            "description": module.get("description"),
            "difficulty": module.get("difficulty"),
            "estimated_effort_band": module.get("estimated_effort_band"),
            "expected_retry_density": module.get("expected_retry_density"),
            "cognitive_load": module.get("cognitive_load"),
            "decay_risk": module.get("decay_risk"),
            "interview_frequency": module.get("interview_frequency"),
            "current_mastery_state": module.get("current_mastery_state", "not_started"),
            "tags": module.get("tags", []),
            "prerequisites": module.get("prerequisites", []),
            "depends_on": module.get("depends_on", []),
            "suggested_order": module.get("suggested_order"),
            "suggested_order_semantics": "weak_tiebreaker",
        },
        "retry_metadata": {
            "retry_count": 0,
            "last_attempted_at": None,
            "last_completed_at": None,
            "last_reviewed_at": None,
            "next_review_at": None,
            "last_confidence": None,
            "error_count": 0,
            "failure_reason": None,
            "struggle_tags": [],
            "retry_priority": 0,
        },
    }


def lesson_metadata(
    manifest: dict[str, Any],
    track: dict[str, Any],
    module: dict[str, Any],
    lesson: dict[str, Any],
) -> dict[str, Any]:
    meta = manifest.get("meta", {})
    return {
        "kind": "lesson",
        "manifest_id": meta.get("id") if isinstance(meta, dict) else None,
        "schema_version": meta.get("schema_version") if isinstance(meta, dict) else None,
        "track_id": track.get("id"),
        "module_id": module.get("id"),
        "lesson_id": lesson.get("id"),
        "lesson_type": lesson.get("type"),
        "difficulty": lesson.get("difficulty"),
        "cognitive_load": lesson.get("cognitive_load"),
        "mastery_state": lesson.get("mastery_state", "not_started"),
        "retry_metadata": lesson.get(
            "retry_metadata",
            {
                "retry_count": 0,
                "last_attempted_at": None,
                "last_completed_at": None,
                "last_reviewed_at": None,
                "next_review_at": None,
                "last_confidence": None,
                "error_count": 0,
                "failure_reason": None,
                "struggle_tags": [],
                "retry_priority": 0,
            },
        ),
        "estimated_effort_band": lesson.get("estimated_effort_band"),
        "completion_criteria": lesson.get("completion_criteria"),
        "estimated_minutes": lesson.get("estimated_minutes"),
        "source": lesson.get("source"),
        "inferred": lesson.get("inferred", False),
        "depends_on": lesson.get("depends_on", []),
        "suggested_order": lesson.get("suggested_order"),
        "suggested_order_semantics": "weak_tiebreaker",
    }


def lesson_tags(lesson: dict[str, Any], module: dict[str, Any]) -> list[str]:
    lesson_type = lesson.get("type", {})
    category = lesson_type.get("category") if isinstance(lesson_type, dict) else None
    tags = ["curriculum", f"lesson:{lesson['id']}", f"module:{module['id']}"]
    if isinstance(category, str):
        tags.append(category)
    cognitive_load = lesson.get("cognitive_load")
    if isinstance(cognitive_load, str):
        tags.append(f"load:{cognitive_load}")
    return tags


def asset_source_path(asset: dict[str, Any]) -> Path | None:
    source = asset.get("source")
    if not isinstance(source, dict):
        return None
    repo = source.get("repo")
    path = source.get("path")
    if not isinstance(repo, str) or not isinstance(path, str):
        return None
    return REPO_ROOT / "curriculum" / "sources" / repo / path


def collect_asset_warnings(manifest: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        return warnings
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        source_path = asset_source_path(asset)
        if source_path is None or not source_path.exists():
            warnings.append(
                f"asset {asset.get('id', '<missing-id>')} source file is missing: {source_path}"
            )
    return warnings


async def row_exists(sql: str, *args: Any) -> bool:
    row = await db.fetchrow(sql, *args)
    return row is not None


async def upsert_course(
    manifest: dict[str, Any], course_code: str, dry_run: bool, counts: ActionCounts
) -> None:
    meta = manifest["meta"]
    notes = metadata_text("course", meta["id"], course_metadata(manifest, course_code))
    exists = await row_exists("SELECT 1 FROM courses WHERE code = %s", course_code)
    if dry_run:
        counts.update += int(exists)
        counts.create += int(not exists)
        return
    if exists:
        await db.execute(
            "UPDATE courses SET full_name = %s, short_name = %s, module_code = %s, "
            "status_kind = %s, language = %s, folder_name = %s, notes = %s "
            "WHERE code = %s",
            meta.get("name", "Interview Engineering"),
            "Interview Engineering",
            meta.get("id"),
            "active",
            "en",
            "interview-engineering",
            notes,
            course_code,
        )
        counts.update += 1
    else:
        await db.execute(
            "INSERT INTO courses "
            "(code, full_name, short_name, module_code, status_kind, language, folder_name, notes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            course_code,
            meta.get("name", "Interview Engineering"),
            "Interview Engineering",
            meta.get("id"),
            "active",
            "en",
            "interview-engineering",
            notes,
        )
        counts.create += 1


async def find_marked_row(
    table: str,
    text_column: str,
    kind: str,
    stable_id: str,
    course_code: str,
    dry_run: bool,
) -> dict[str, Any] | None:
    rows = await db.fetch(
        f"SELECT * FROM {table} WHERE course_code = %s AND {text_column} LIKE %s "
        "ORDER BY updated_at NULLS LAST, created_at, id",
        course_code,
        f"{marker(kind, stable_id)}\n%",
    )
    if len(rows) > 1 and not dry_run:
        for duplicate in rows[1:]:
            await db.execute(f"DELETE FROM {table} WHERE id = %s", duplicate["id"])
    return rows[0] if rows else None


async def upsert_module(
    manifest: dict[str, Any],
    track: dict[str, Any],
    module: dict[str, Any],
    course_code: str,
    dry_run: bool,
    counts: ActionCounts,
) -> None:
    module_id = module["id"]
    notes = metadata_text("module", module_id, module_metadata(manifest, track, module))
    row = await find_marked_row(
        "study_topics", "notes", "module", module_id, course_code, dry_run
    )
    if dry_run:
        counts.update += int(row is not None)
        counts.create += int(row is None)
        return
    if row:
        await db.execute(
            "UPDATE study_topics SET course_code = %s, chapter = %s, name = %s, "
            "description = %s, kind = %s, status = %s, mastery_state = %s, "
            "retry_count = %s, error_count = %s, retry_priority = %s, "
            "notes = %s, sort_order = %s "
            "WHERE id = %s",
            course_code,
            track.get("name"),
            module.get("name"),
            module.get("description"),
            "reading",
            row.get("status", "not_started") or "not_started",
            module.get("current_mastery_state", "not_started"),
            row.get("retry_count", 0) or 0,
            row.get("error_count", 0) or 0,
            row.get("retry_priority", 0) or 0,
            notes,
            module.get("suggested_order", 0),
            row["id"],
        )
        counts.update += 1
    else:
        await db.execute(
            "INSERT INTO study_topics "
            "(course_code, chapter, name, description, kind, status, mastery_state, notes, sort_order) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            course_code,
            track.get("name"),
            module.get("name"),
            module.get("description"),
            "reading",
            "not_started",
            module.get("current_mastery_state", "not_started"),
            notes,
            module.get("suggested_order", 0),
        )
        counts.create += 1


async def upsert_lesson(
    manifest: dict[str, Any],
    track: dict[str, Any],
    module: dict[str, Any],
    lesson: dict[str, Any],
    course_code: str,
    dry_run: bool,
    counts: ActionCounts,
) -> None:
    lesson_id = lesson["id"]
    metadata = lesson_metadata(manifest, track, module, lesson)
    description = metadata_text("lesson", lesson_id, metadata)
    row = await find_marked_row(
        "tasks", "description", "lesson", lesson_id, course_code, dry_run
    )
    if dry_run:
        counts.update += int(row is not None)
        counts.create += int(row is None)
        return
    priority = "high" if lesson.get("cognitive_load") == "high" else "med"
    if row:
        await db.execute(
            "UPDATE tasks SET course_code = %s, title = %s, description = %s, "
            "status = %s, priority = %s, tags = %s, mastery_state = %s, "
            "retry_count = %s, error_count = %s, retry_priority = %s WHERE id = %s",
            course_code,
            lesson.get("title"),
            description,
            row.get("status", "open") or "open",
            priority,
            lesson_tags(lesson, module),
            row.get("mastery_state") or metadata["mastery_state"],
            row.get("retry_count", 0) or 0,
            row.get("error_count", 0) or 0,
            row.get("retry_priority", 0) or 0,
            row["id"],
        )
        counts.update += 1
    else:
        await db.execute(
            "INSERT INTO tasks (course_code, title, description, status, priority, tags, mastery_state) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            course_code,
            lesson.get("title"),
            description,
            "open",
            priority,
            lesson_tags(lesson, module),
            metadata["mastery_state"],
        )
        counts.create += 1


async def replace_event(
    kind: str,
    course_code: str,
    stable_id: str,
    payload: dict[str, Any],
    dry_run: bool,
    counts: ActionCounts,
) -> None:
    rows = await db.fetch(
        "SELECT id FROM events WHERE kind = %s AND course_code = %s "
        "AND payload->>'seed_id' = %s ORDER BY created_at, id",
        kind,
        course_code,
        stable_id,
    )
    if dry_run:
        counts.update += int(bool(rows))
        counts.create += int(not rows)
        return
    if rows:
        keeper = rows[0]["id"]
        await db.execute(
            "UPDATE events SET payload = %s::jsonb WHERE id = %s",
            compact_json(payload),
            keeper,
        )
        for duplicate in rows[1:]:
            await db.execute("DELETE FROM events WHERE id = %s", duplicate["id"])
        counts.update += 1
        counts.delete += len(rows) - 1
    else:
        await db.execute(
            "INSERT INTO events (kind, course_code, payload) VALUES (%s, %s, %s::jsonb)",
            kind,
            course_code,
            compact_json(payload),
        )
        counts.create += 1


async def seed_events(
    manifest: dict[str, Any], course_code: str, dry_run: bool, plan: SeedPlan
) -> None:
    meta = manifest["meta"]
    for source in manifest.get("sources", []):
        payload = {
            "seed_id": source["id"],
            "kind": "source",
            "manifest_id": meta["id"],
            "schema_version": meta["schema_version"],
            "source": source,
        }
        await replace_event(
            "curriculum:source", course_code, source["id"], payload, dry_run, plan.sources
        )
    for asset in manifest.get("assets", []):
        payload = {
            "seed_id": asset["id"],
            "kind": "asset",
            "manifest_id": meta["id"],
            "schema_version": meta["schema_version"],
            "asset": asset,
        }
        await replace_event(
            "curriculum:asset", course_code, asset["id"], payload, dry_run, plan.assets
        )
    for track in iter_tracks(manifest):
        payload = {
            "seed_id": track["id"],
            "kind": "track",
            "manifest_id": meta["id"],
            "schema_version": meta["schema_version"],
            "track": {
                "id": track.get("id"),
                "name": track.get("name"),
                "description": track.get("description"),
                "audience": track.get("audience"),
                "suggested_order": track.get("suggested_order"),
                "depends_on": track.get("depends_on", []),
            },
        }
        await replace_event(
            "curriculum:track", course_code, track["id"], payload, dry_run, plan.tracks
        )


async def reset_seeded_data(course_code: str, dry_run: bool, plan: SeedPlan) -> None:
    statements = [
        ("tasks", "DELETE FROM tasks WHERE course_code = %s AND description LIKE %s"),
        ("study_topics", "DELETE FROM study_topics WHERE course_code = %s AND notes LIKE %s"),
        (
            "events",
            "DELETE FROM events WHERE course_code = %s AND kind LIKE 'curriculum:%'",
        ),
        ("courses", "DELETE FROM courses WHERE code = %s"),
    ]
    for _, sql in statements:
        if "LIKE %s" in sql:
            rows = await db.fetch(
                sql.replace("DELETE", "SELECT *"), course_code, f"{MARKER_PREFIX}%"
            )
            plan.lessons.delete += len(rows) if "tasks" in sql else 0
            plan.modules.delete += len(rows) if "study_topics" in sql else 0
            if not dry_run:
                await db.execute(sql, course_code, f"{MARKER_PREFIX}%")
        elif "events" in sql:
            rows = await db.fetch(
                "SELECT * FROM events WHERE course_code = %s AND kind LIKE 'curriculum:%'",
                course_code,
            )
            plan.sources.delete += len(rows)
            if not dry_run:
                await db.execute(sql, course_code)
        else:
            exists = await row_exists("SELECT 1 FROM courses WHERE code = %s", course_code)
            if exists:
                plan.courses.delete += 1
            if not dry_run:
                await db.execute(sql, course_code)


async def seed_manifest_data(
    manifest: dict[str, Any],
    *,
    course_code: str | None = None,
    dry_run: bool = False,
    reset: bool = False,
) -> SeedPlan:
    resolved_course_code = stable_course_code(manifest, course_code)
    manifest_id = manifest["meta"]["id"]
    plan = SeedPlan(
        course_code=resolved_course_code,
        manifest_id=manifest_id,
        dry_run=dry_run,
        dependencies=dependency_count(manifest),
        warnings=collect_asset_warnings(manifest),
    )
    if reset:
        await reset_seeded_data(resolved_course_code, dry_run, plan)
    await upsert_course(manifest, resolved_course_code, dry_run, plan.courses)
    await seed_events(manifest, resolved_course_code, dry_run, plan)
    for track, module in iter_modules(manifest):
        await upsert_module(manifest, track, module, resolved_course_code, dry_run, plan.modules)
    for track, module, lesson in iter_lessons(manifest):
        await upsert_lesson(
            manifest, track, module, lesson, resolved_course_code, dry_run, plan.lessons
        )
    return plan


def print_counts(label: str, counts: ActionCounts) -> None:
    print(
        f"{label}: create={counts.create}, update={counts.update}, "
        f"skip={counts.skip}, delete={counts.delete}"
    )


def print_plan(plan: SeedPlan, verbose: bool) -> None:
    mode = "Dry run" if plan.dry_run else "Seed"
    print(f"{mode} complete.")
    print(f"Course code: {plan.course_code}")
    print(f"Manifest id: {plan.manifest_id}")
    print_counts("Courses", plan.courses)
    print_counts("Sources", plan.sources)
    print_counts("Assets", plan.assets)
    print_counts("Tracks", plan.tracks)
    print_counts("Modules", plan.modules)
    print_counts("Lessons/tasks", plan.lessons)
    print(f"Dependencies recorded in metadata: {plan.dependencies}")
    if plan.warnings:
        print("Warnings:")
        for warning in plan.warnings:
            print(f"  - {warning}")
    elif verbose:
        print("Warnings: 0")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed an OpenStudy curriculum manifest.")
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help=f"Manifest path (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan without writing to the DB.")
    parser.add_argument("--verbose", action="store_true", help="Print detailed counts.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Remove previously seeded data for this course before reseeding.",
    )
    parser.add_argument(
        "--course-code",
        default=None,
        help=f"OpenStudy course code to seed into (default: {DEFAULT_COURSE_CODE}).",
    )
    parser.add_argument(
        "--allow-missing-source-files",
        action="store_true",
        help=(
            "Allow seeding from a packaged manifest when curriculum/sources files "
            "are intentionally absent from the runtime image."
        ),
    )
    return parser.parse_args(argv)


async def async_main(argv: list[str]) -> int:
    args = parse_args(argv)
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path
    try:
        manifest = read_manifest(
            manifest_path,
            allow_missing_source_files=args.allow_missing_source_files,
        )
    except ValueError as exc:
        print(f"Could not seed manifest: {exc}", file=sys.stderr)
        return 2

    await db.init_pool()
    try:
        plan = await seed_manifest_data(
            manifest,
            course_code=args.course_code,
            dry_run=args.dry_run,
            reset=args.reset,
        )
    finally:
        await db.close_pool()
    print_plan(plan, args.verbose)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
