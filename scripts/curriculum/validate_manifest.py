"""Validate schema v2 curriculum manifests.

Run from the repo root:
    python scripts/curriculum/validate_manifest.py

Or validate a specific manifest:
    python scripts/curriculum/validate_manifest.py curriculum/interview_manifest.v2.yaml
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, cast
from urllib.parse import urlparse

import yaml


Severity = Literal["error", "warning"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path("curriculum/interview_manifest.v2.yaml")

KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKDOWN_HEADING_RE = re.compile(r"^\s*(?:[-*+]\s+)?#{1,6}\s+(.+?)\s*#*\s*$")
VALID_DIFFICULTY_LEVELS = {"beginner", "intermediate", "advanced"}
VALID_COGNITIVE_LOADS = {"low", "medium", "high"}
VALID_TYPE_CATEGORIES = {"reading", "coding-exercise", "project", "checkpoint"}
VALID_TYPE_MEDIA = {"markdown", "notebook", "video", "reference", "manifest"}
VALID_TYPE_INTERACTIONS = {"passive", "active", "constructive", "reflective"}
VALID_ASSET_KINDS = {"flashcard-deck", "flashcard-database"}
VALID_ASSET_FORMATS = {"apkg", "db"}
LESSON_ID_PREFIXES = ("ciu-", "icc-", "pc-", "pcpp-", "py-", "sdp-", "ie-")
GENERIC_TAGS = {"misc", "general"}

TOP_LEVEL_KEYS = {"meta", "sources", "tracks", "assets"}
REQUIRED_TOP_LEVEL_KEYS = {"meta", "sources", "tracks"}
META_KEYS = {
    "id",
    "name",
    "description",
    "timezone",
    "version",
    "owner",
    "format",
    "status",
    "estimated_total_hours",
    "attribution_note",
    "schema_version",
    "id_policy",
}
SOURCE_REQUIRED_KEYS = {"id", "name", "path", "upstream"}
SOURCE_KEYS = SOURCE_REQUIRED_KEYS | {"kind", "course_code"}
ASSET_KEYS = {"id", "title", "kind", "format", "course_code", "source", "storage_path", "usage"}
ASSET_USAGE_KEYS = {"openstudy_readable", "import_into_anki", "review_cadence"}
TRACK_KEYS = {"id", "name", "description", "audience", "suggested_order", "depends_on", "modules"}
MODULE_KEYS = {
    "id",
    "name",
    "description",
    "difficulty",
    "estimated_hours",
    "tags",
    "prerequisites",
    "depends_on",
    "suggested_order",
    "lessons",
}
LESSON_REQUIRED_KEYS = {
    "id",
    "title",
    "type",
    "source",
    "estimated_minutes",
    "completion_criteria",
    "suggested_order",
    "depends_on",
    "difficulty",
    "cognitive_load",
}
LESSON_KEYS = LESSON_REQUIRED_KEYS | {"inferred"}
LESSON_TYPE_KEYS = {"category", "medium", "interaction"}
DIFFICULTY_KEYS = {"level", "score"}
SOURCE_REF_KEYS = {"repo", "path", "heading"}


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    category: str
    path: str
    message: str


@dataclass
class ValidationStats:
    sources: int = 0
    tracks: int = 0
    modules: int = 0
    lessons: int = 0
    projects: int = 0
    checkpoints: int = 0
    estimated_minutes: int = 0

    @property
    def estimated_hours(self) -> float:
        return self.estimated_minutes / 60


@dataclass
class ValidationResult:
    issues: list[ValidationIssue]
    stats: ValidationStats
    schema_version: Any = None

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]


@dataclass(frozen=True)
class ValidationOptions:
    allow_missing_source_files: bool = False


def issue(severity: Severity, category: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(severity=severity, category=category, path=path, message=message)


def load_manifest(path: Path) -> tuple[dict[str, Any] | None, int]:
    """Load a manifest. Returns (data, exit_code_if_failed)."""
    if not path.exists():
        print(f"Could not read manifest: file does not exist: {path}", file=sys.stderr)
        return None, 2
    if not path.is_file():
        print(f"Could not read manifest: path is not a file: {path}", file=sys.stderr)
        return None, 2
    try:
        with path.open("r", encoding="utf-8") as manifest_file:
            data = yaml.safe_load(manifest_file)
    except yaml.YAMLError as exc:
        print(f"Could not parse YAML: {path}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return None, 2
    except OSError as exc:
        print(f"Could not read manifest: {path}: {exc}", file=sys.stderr)
        return None, 2

    if not isinstance(data, dict):
        print(f"Manifest YAML must be a mapping/dictionary: {path}", file=sys.stderr)
        return None, 2
    return data, 0


def is_kebab_case(value: Any) -> bool:
    return isinstance(value, str) and bool(KEBAB_CASE_RE.fullmatch(value))


def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def looks_like_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().strip("#").strip()).casefold()


def markdown_headings(path: Path) -> set[str]:
    headings: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = MARKDOWN_HEADING_RE.match(line)
            if match:
                headings.add(normalize_heading(match.group(1)))
    except OSError:
        return headings
    return headings


def add_missing_key_errors(
    issues: list[ValidationIssue], obj: Any, required: Iterable[str], path: str, category: str
) -> None:
    if not isinstance(obj, dict):
        return
    for key in required:
        if key not in obj:
            issues.append(issue("error", category, f"{path}.{key}", "missing required key"))


def warn_extra_keys(
    issues: list[ValidationIssue], obj: Any, expected: set[str], path: str, category: str
) -> None:
    if not isinstance(obj, dict):
        return
    for key in sorted(set(obj) - expected):
        issues.append(
            issue(
                "warning", category, f"{path}.{key}", "unexpected extra key; importer may ignore it"
            )
        )


def require_mapping(
    issues: list[ValidationIssue], value: Any, path: str, category: str
) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    issues.append(issue("error", category, path, "must be a mapping/dictionary"))
    return None


def require_list(
    issues: list[ValidationIssue], value: Any, path: str, category: str, *, non_empty: bool = False
) -> list[Any] | None:
    if not isinstance(value, list):
        issues.append(issue("error", category, path, "must be a list"))
        return None
    if non_empty and not value:
        issues.append(issue("error", category, path, "must be a non-empty list"))
    return value


def check_required_text(
    issues: list[ValidationIssue], obj: dict[str, Any], key: str, path: str, category: str
) -> None:
    if key in obj and not is_non_empty_string(obj[key]):
        issues.append(issue("error", category, f"{path}.{key}", "must be a non-empty string"))


def check_id(
    issues: list[ValidationIssue],
    value: Any,
    path: str,
    category: str,
    ids: dict[str, str],
    *,
    lesson_prefix_warning: bool = False,
) -> None:
    if not is_non_empty_string(value):
        issues.append(issue("error", category, path, "must be a non-empty string"))
        return
    if not is_kebab_case(value):
        issues.append(issue("error", category, path, "must use lowercase kebab-case"))
    if value in ids:
        issues.append(
            issue("error", "schema-hygiene", path, f"duplicate id also defined at {ids[value]}")
        )
    else:
        ids[value] = path
    if (
        lesson_prefix_warning
        and isinstance(value, str)
        and not value.startswith(LESSON_ID_PREFIXES)
    ):
        issues.append(
            issue(
                "warning",
                "schema-hygiene",
                path,
                "lesson id should preferably start with a known stable source prefix",
            )
        )


def validate_difficulty(
    issues: list[ValidationIssue], value: Any, path: str, category: str
) -> None:
    difficulty = require_mapping(issues, value, path, category)
    if difficulty is None:
        return
    add_missing_key_errors(issues, difficulty, DIFFICULTY_KEYS, path, category)
    warn_extra_keys(issues, difficulty, DIFFICULTY_KEYS, path, category)
    level = difficulty.get("level")
    score = difficulty.get("score")
    if level is not None and level not in VALID_DIFFICULTY_LEVELS:
        issues.append(
            issue(
                "error",
                category,
                f"{path}.level",
                f"must be one of {', '.join(sorted(VALID_DIFFICULTY_LEVELS))}",
            )
        )
    if score is not None:
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
            issues.append(
                issue("error", category, f"{path}.score", "must be an integer between 1 and 10")
            )


def resolve_source_path(
    source_ref: dict[str, Any], source_roots: dict[str, Path], manifest_path: Path
) -> Path | None:
    repo = source_ref.get("repo")
    source_path = source_ref.get("path")
    if repo == "manifest" and source_path in {None, "", str(manifest_path.relative_to(REPO_ROOT))}:
        return manifest_path
    if not isinstance(repo, str) or not isinstance(source_path, str):
        return None
    root = source_roots.get(repo)
    if root is None:
        return None
    return root / source_path


def collect_graph_edge(
    issues: list[ValidationIssue],
    graph: dict[str, list[str]],
    owner_id: Any,
    deps: Any,
    path: str,
    known_ids: dict[str, str],
    category: str,
) -> None:
    if owner_id is None:
        return
    if not isinstance(deps, list):
        issues.append(issue("error", category, path, "must be a list"))
        return
    owner = str(owner_id)
    graph.setdefault(owner, [])
    for index, dep in enumerate(deps):
        dep_path = f"{path}[{index}]"
        if not is_non_empty_string(dep):
            issues.append(
                issue("error", category, dep_path, "dependency id must be a non-empty string")
            )
            continue
        if dep == owner:
            issues.append(
                issue("error", "dependency-graph", dep_path, "self-dependency is not allowed")
            )
            continue
        if dep not in known_ids:
            issues.append(
                issue("error", "dependency-graph", dep_path, f"unknown dependency id: {dep}")
            )
            continue
        graph[owner].append(dep)


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            start = stack.index(node)
            return stack[start:] + [node]
        if node in visited:
            return None
        visiting.add(node)
        stack.append(node)
        for dep in graph.get(node, []):
            cycle = visit(dep)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return None


def validate_manifest(
    data: dict[str, Any],
    manifest_path: Path,
    options: ValidationOptions | None = None,
) -> ValidationResult:
    options = options or ValidationOptions()
    issues: list[ValidationIssue] = []
    stats = ValidationStats()
    known_ids: dict[str, str] = {}
    source_ids: dict[str, str] = {}
    source_roots: dict[str, Path] = {}
    graph: dict[str, list[str]] = {}
    module_order_by_track: dict[str, dict[str, int]] = {}
    lesson_order_by_module: dict[str, dict[str, int]] = {}

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            issues.append(issue("error", "yaml", key, "missing required top-level key"))
    if options.allow_missing_source_files:
        issues.append(
            issue(
                "warning",
                "sources",
                "$.sources",
                "source file existence checks skipped because --allow-missing-source-files was set",
            )
        )
    warn_extra_keys(issues, data, TOP_LEVEL_KEYS, "$", "schema-hygiene")

    meta = require_mapping(issues, data.get("meta"), "meta", "meta")
    schema_version = meta.get("schema_version") if meta else None
    if meta is not None:
        add_missing_key_errors(
            issues, meta, {"id", "name", "schema_version", "timezone"}, "meta", "meta"
        )
        warn_extra_keys(issues, meta, META_KEYS, "meta", "schema-hygiene")
        for key in ("id", "name", "timezone"):
            check_required_text(issues, meta, key, "meta", "meta")
        if meta.get("schema_version") != 2:
            issues.append(issue("error", "meta", "meta.schema_version", "must equal 2"))
        if meta.get("schema_version") == 2 and "id_policy" not in meta:
            issues.append(issue("error", "meta", "meta.id_policy", "required for schema_version 2"))

    sources = require_list(issues, data.get("sources"), "sources", "sources", non_empty=True)
    if sources is not None:
        stats.sources = len(sources)
        for source_index, raw_source in enumerate(sources):
            source_path = f"sources[{source_index}]"
            source = require_mapping(issues, raw_source, source_path, "sources")
            if source is None:
                continue
            add_missing_key_errors(issues, source, SOURCE_REQUIRED_KEYS, source_path, "sources")
            warn_extra_keys(issues, source, SOURCE_KEYS, source_path, "schema-hygiene")
            for key in SOURCE_REQUIRED_KEYS:
                if key in source:
                    check_required_text(issues, source, key, source_path, "sources")
            source_id = source.get("id")
            if source_id is not None:
                if not is_kebab_case(source_id):
                    issues.append(
                        issue(
                            "error", "sources", f"{source_path}.id", "must use lowercase kebab-case"
                        )
                    )
                if source_id in source_ids:
                    issues.append(
                        issue(
                            "error",
                            "sources",
                            f"{source_path}.id",
                            f"duplicate source id also defined at {source_ids[source_id]}",
                        )
                    )
                elif isinstance(source_id, str):
                    source_ids[source_id] = f"{source_path}.id"
            path_value = source.get("path")
            if isinstance(path_value, str):
                resolved = REPO_ROOT / path_value
                if not resolved.exists():
                    if not options.allow_missing_source_files:
                        issues.append(
                            issue(
                                "error",
                                "sources",
                                f"{source_path}.path",
                                "source path does not exist",
                            )
                        )
                elif not resolved.is_dir():
                    issues.append(
                        issue(
                            "error",
                            "sources",
                            f"{source_path}.path",
                            "source path must be a directory",
                        )
                    )
                if isinstance(source_id, str):
                    source_roots[source_id] = resolved
            if "upstream" in source and not looks_like_url(source["upstream"]):
                issues.append(
                    issue(
                        "error", "sources", f"{source_path}.upstream", "must look like an HTTP URL"
                    )
                )

    if "assets" in data:
        validate_assets(issues, data.get("assets"), source_ids, source_roots, options)

    tracks = require_list(issues, data.get("tracks"), "tracks", "tracks", non_empty=True)
    if tracks is not None:
        stats.tracks = len(tracks)
        for track_index, raw_track in enumerate(tracks):
            track_path = f"tracks[{track_index}]"
            track = require_mapping(issues, raw_track, track_path, "tracks")
            if track is None:
                continue
            add_missing_key_errors(
                issues, track, {"id", "name", "description", "modules"}, track_path, "tracks"
            )
            warn_extra_keys(issues, track, TRACK_KEYS, track_path, "schema-hygiene")
            for key in ("name", "description"):
                check_required_text(issues, track, key, track_path, "tracks")
            track_id = track.get("id")
            check_id(issues, track_id, f"{track_path}.id", "tracks", known_ids)
            modules = require_list(
                issues, track.get("modules"), f"{track_path}.modules", "tracks", non_empty=True
            )
            if modules is None:
                continue
            if isinstance(track_id, str):
                module_order_by_track[track_id] = {}
            stats.modules += len(modules)
            expected_order = 1
            for module_index, raw_module in enumerate(modules):
                module_path = f"{track_path}.modules[{module_index}]"
                module = require_mapping(issues, raw_module, module_path, "modules")
                if module is None:
                    continue
                validate_module(
                    issues,
                    stats,
                    module,
                    module_path,
                    module_index,
                    expected_order,
                    known_ids,
                    track_id if isinstance(track_id, str) else "",
                    module_order_by_track,
                    lesson_order_by_module,
                    source_ids,
                    source_roots,
                    manifest_path,
                    options,
                )
                expected_order += 1

    if tracks is not None:
        for track_index, raw_track in enumerate(tracks):
            if not isinstance(raw_track, dict):
                continue
            track_id = raw_track.get("id")
            collect_graph_edge(
                issues,
                graph,
                track_id,
                raw_track.get("depends_on", []),
                f"tracks[{track_index}].depends_on",
                known_ids,
                "tracks",
            )
            modules = raw_track.get("modules", [])
            if isinstance(modules, list):
                for module_index, raw_module in enumerate(modules):
                    if not isinstance(raw_module, dict):
                        continue
                    module_path = f"tracks[{track_index}].modules[{module_index}]"
                    module_id = raw_module.get("id")
                    collect_graph_edge(
                        issues,
                        graph,
                        module_id,
                        raw_module.get("depends_on", []),
                        f"{module_path}.depends_on",
                        known_ids,
                        "modules",
                    )
                    collect_graph_edge(
                        issues,
                        graph,
                        module_id,
                        raw_module.get("prerequisites", []),
                        f"{module_path}.prerequisites",
                        known_ids,
                        "modules",
                    )
                    warn_later_module_dependencies(
                        issues,
                        raw_track.get("id"),
                        module_id,
                        raw_module.get("depends_on", []),
                        module_order_by_track,
                        f"{module_path}.depends_on",
                    )
                    lessons = raw_module.get("lessons", [])
                    if isinstance(lessons, list):
                        for lesson_index, raw_lesson in enumerate(lessons):
                            if not isinstance(raw_lesson, dict):
                                continue
                            lesson_path = f"{module_path}.lessons[{lesson_index}]"
                            lesson_id = raw_lesson.get("id")
                            collect_graph_edge(
                                issues,
                                graph,
                                lesson_id,
                                raw_lesson.get("depends_on", []),
                                f"{lesson_path}.depends_on",
                                known_ids,
                                "lessons",
                            )
                            warn_later_lesson_dependencies(
                                issues,
                                module_id,
                                lesson_id,
                                raw_lesson.get("depends_on", []),
                                lesson_order_by_module,
                                f"{lesson_path}.depends_on",
                            )

    cycle = find_cycle(graph)
    if cycle:
        issues.append(
            issue(
                "error",
                "dependency-graph",
                "depends_on",
                f"circular dependency: {' -> '.join(cycle)}",
            )
        )

    return ValidationResult(issues=issues, stats=stats, schema_version=schema_version)


def validate_assets(
    issues: list[ValidationIssue],
    raw_assets: Any,
    source_ids: dict[str, str],
    source_roots: dict[str, Path],
    options: ValidationOptions,
) -> None:
    assets = require_list(issues, raw_assets, "assets", "assets")
    if assets is None:
        return
    asset_ids: dict[str, str] = {}
    for asset_index, raw_asset in enumerate(assets):
        asset_path = f"assets[{asset_index}]"
        asset = require_mapping(issues, raw_asset, asset_path, "assets")
        if asset is None:
            continue
        add_missing_key_errors(issues, asset, ASSET_KEYS, asset_path, "assets")
        warn_extra_keys(issues, asset, ASSET_KEYS, asset_path, "schema-hygiene")
        for key in ("title", "kind", "format", "course_code", "storage_path"):
            check_required_text(issues, asset, key, asset_path, "assets")

        asset_id = asset.get("id")
        if not is_non_empty_string(asset_id):
            issues.append(
                issue("error", "assets", f"{asset_path}.id", "must be a non-empty string")
            )
        elif not is_kebab_case(asset_id):
            issues.append(
                issue("error", "assets", f"{asset_path}.id", "must use lowercase kebab-case")
            )
        elif asset_id in asset_ids:
            issues.append(
                issue(
                    "error",
                    "assets",
                    f"{asset_path}.id",
                    f"duplicate asset id also defined at {asset_ids[asset_id]}",
                )
            )
        elif isinstance(asset_id, str):
            asset_ids[asset_id] = f"{asset_path}.id"

        if "kind" in asset and asset["kind"] not in VALID_ASSET_KINDS:
            issues.append(
                issue(
                    "error",
                    "assets",
                    f"{asset_path}.kind",
                    f"must be one of {', '.join(sorted(VALID_ASSET_KINDS))}",
                )
            )
        if "format" in asset and asset["format"] not in VALID_ASSET_FORMATS:
            issues.append(
                issue(
                    "error",
                    "assets",
                    f"{asset_path}.format",
                    f"must be one of {', '.join(sorted(VALID_ASSET_FORMATS))}",
                )
            )

        source = require_mapping(issues, asset.get("source"), f"{asset_path}.source", "assets")
        if source is not None:
            warn_extra_keys(
                issues, source, SOURCE_REF_KEYS, f"{asset_path}.source", "schema-hygiene"
            )
            repo = source.get("repo")
            source_path = source.get("path")
            if repo not in source_ids:
                issues.append(
                    issue(
                        "error",
                        "assets",
                        f"{asset_path}.source.repo",
                        "must reference a known source id",
                    )
                )
            elif not isinstance(source_path, str) or not source_path.strip():
                issues.append(
                    issue(
                        "error", "assets", f"{asset_path}.source.path", "must be a non-empty string"
                    )
                )
            else:
                resolved = source_roots[repo] / source_path
                try:
                    resolved.resolve().relative_to(source_roots[repo].resolve())
                except ValueError:
                    issues.append(
                        issue(
                            "error",
                            "assets",
                            f"{asset_path}.source.path",
                            "must stay inside the declared source repository",
                        )
                    )
                if not resolved.exists():
                    if not options.allow_missing_source_files:
                        issues.append(
                            issue(
                                "error",
                                "assets",
                                f"{asset_path}.source.path",
                                "asset source path does not exist",
                            )
                        )

        usage = require_mapping(issues, asset.get("usage"), f"{asset_path}.usage", "assets")
        if usage is not None:
            warn_extra_keys(
                issues, usage, ASSET_USAGE_KEYS, f"{asset_path}.usage", "schema-hygiene"
            )
            for key in ("openstudy_readable", "import_into_anki"):
                if key not in usage:
                    issues.append(
                        issue(
                            "error", "assets", f"{asset_path}.usage.{key}", "missing required key"
                        )
                    )
                elif not isinstance(usage[key], bool):
                    issues.append(
                        issue("error", "assets", f"{asset_path}.usage.{key}", "must be a boolean")
                    )
            if "review_cadence" in usage:
                check_required_text(
                    issues, usage, "review_cadence", f"{asset_path}.usage", "assets"
                )


def validate_module(
    issues: list[ValidationIssue],
    stats: ValidationStats,
    module: dict[str, Any],
    module_path: str,
    module_index: int,
    expected_order: int,
    known_ids: dict[str, str],
    track_id: str,
    module_order_by_track: dict[str, dict[str, int]],
    lesson_order_by_module: dict[str, dict[str, int]],
    source_ids: dict[str, str],
    source_roots: dict[str, Path],
    manifest_path: Path,
    options: ValidationOptions,
) -> None:
    add_missing_key_errors(issues, module, MODULE_KEYS, module_path, "modules")
    warn_extra_keys(issues, module, MODULE_KEYS, module_path, "schema-hygiene")
    for key in ("name", "description"):
        check_required_text(issues, module, key, module_path, "modules")
    module_id = module.get("id")
    check_id(issues, module_id, f"{module_path}.id", "modules", known_ids)
    if isinstance(module_id, str):
        module_order_by_track.setdefault(track_id, {})[module_id] = module_index + 1
        lesson_order_by_module[module_id] = {}
    if module.get("suggested_order") != expected_order:
        issues.append(
            issue(
                "error",
                "modules",
                f"{module_path}.suggested_order",
                f"must be sequential starting at 1; expected {expected_order}",
            )
        )
    if "estimated_hours" in module and not is_positive_number(module["estimated_hours"]):
        issues.append(
            issue("error", "modules", f"{module_path}.estimated_hours", "must be a positive number")
        )
    tags = module.get("tags")
    if isinstance(tags, list):
        if not tags:
            issues.append(
                issue("warning", "schema-hygiene", f"{module_path}.tags", "tags list is empty")
            )
        for tag_index, tag in enumerate(tags):
            tag_path = f"{module_path}.tags[{tag_index}]"
            if not is_kebab_case(tag):
                issues.append(
                    issue("error", "modules", tag_path, "tag must use lowercase kebab-case")
                )
            if tag in GENERIC_TAGS:
                issues.append(issue("warning", "schema-hygiene", tag_path, "tag is overly generic"))
    elif "tags" in module:
        issues.append(issue("error", "modules", f"{module_path}.tags", "must be a list"))
    if "prerequisites" in module and not isinstance(module["prerequisites"], list):
        issues.append(issue("error", "modules", f"{module_path}.prerequisites", "must be a list"))
    if "depends_on" in module and not isinstance(module["depends_on"], list):
        issues.append(issue("error", "modules", f"{module_path}.depends_on", "must be a list"))
    validate_difficulty(issues, module.get("difficulty"), f"{module_path}.difficulty", "modules")

    lessons = require_list(
        issues, module.get("lessons"), f"{module_path}.lessons", "modules", non_empty=True
    )
    if lessons is None:
        return
    title_counts: dict[str, int] = defaultdict(int)
    lesson_minutes = 0
    stats.lessons += len(lessons)
    for lesson_index, raw_lesson in enumerate(lessons):
        lesson_path = f"{module_path}.lessons[{lesson_index}]"
        lesson = require_mapping(issues, raw_lesson, lesson_path, "lessons")
        if lesson is None:
            continue
        minutes = validate_lesson(
            issues,
            stats,
            lesson,
            lesson_path,
            lesson_index,
            lesson_index + 1,
            known_ids,
            module_id if isinstance(module_id, str) else "",
            lesson_order_by_module,
            source_ids,
            source_roots,
            manifest_path,
            options,
        )
        lesson_minutes += minutes
        title = lesson.get("title")
        if isinstance(title, str):
            title_counts[title.casefold()] += 1

    for normalized_title, count in title_counts.items():
        if count > 1:
            issues.append(
                issue(
                    "warning",
                    "schema-hygiene",
                    f"{module_path}.lessons",
                    f"repeated lesson title in module: {normalized_title}",
                )
            )
    estimated_hours = module.get("estimated_hours")
    if is_positive_number(estimated_hours) and lesson_minutes > 0:
        estimated_hours_value = float(cast(int | float, estimated_hours))
        lesson_hours = lesson_minutes / 60
        difference = abs(estimated_hours_value - lesson_hours)
        if difference > 1 and difference / max(estimated_hours_value, lesson_hours) > 0.25:
            issues.append(
                issue(
                    "warning",
                    "schema-hygiene",
                    f"{module_path}.estimated_hours",
                    f"differs from lesson sum ({lesson_hours:.1f}h) by more than 25%",
                )
            )


def validate_lesson(
    issues: list[ValidationIssue],
    stats: ValidationStats,
    lesson: dict[str, Any],
    lesson_path: str,
    lesson_index: int,
    expected_order: int,
    known_ids: dict[str, str],
    module_id: str,
    lesson_order_by_module: dict[str, dict[str, int]],
    source_ids: dict[str, str],
    source_roots: dict[str, Path],
    manifest_path: Path,
    options: ValidationOptions,
) -> int:
    add_missing_key_errors(issues, lesson, LESSON_REQUIRED_KEYS, lesson_path, "lessons")
    warn_extra_keys(issues, lesson, LESSON_KEYS, lesson_path, "schema-hygiene")
    for key in ("title", "completion_criteria"):
        check_required_text(issues, lesson, key, lesson_path, "lessons")
    lesson_id = lesson.get("id")
    check_id(
        issues,
        lesson_id,
        f"{lesson_path}.id",
        "lessons",
        known_ids,
        lesson_prefix_warning=True,
    )
    if isinstance(lesson_id, str):
        lesson_order_by_module.setdefault(module_id, {})[lesson_id] = lesson_index + 1
    if lesson.get("suggested_order") != expected_order:
        issues.append(
            issue(
                "error",
                "lessons",
                f"{lesson_path}.suggested_order",
                f"must be sequential starting at 1; expected {expected_order}",
            )
        )
    raw_minutes = lesson.get("estimated_minutes")
    if not is_positive_int(raw_minutes):
        issues.append(
            issue(
                "error", "lessons", f"{lesson_path}.estimated_minutes", "must be a positive integer"
            )
        )
        minutes_value = 0
    else:
        minutes_value = int(cast(int, raw_minutes))
        stats.estimated_minutes += minutes_value
        if minutes_value > 240:
            issues.append(
                issue(
                    "warning",
                    "schema-hygiene",
                    f"{lesson_path}.estimated_minutes",
                    "unusually long lesson",
                )
            )
        if minutes_value < 5:
            issues.append(
                issue(
                    "warning",
                    "schema-hygiene",
                    f"{lesson_path}.estimated_minutes",
                    "unusually short lesson",
                )
            )
    if "depends_on" in lesson and not isinstance(lesson["depends_on"], list):
        issues.append(issue("error", "lessons", f"{lesson_path}.depends_on", "must be a list"))
    validate_difficulty(issues, lesson.get("difficulty"), f"{lesson_path}.difficulty", "lessons")
    cognitive_load = lesson.get("cognitive_load")
    if cognitive_load is not None and cognitive_load not in VALID_COGNITIVE_LOADS:
        issues.append(
            issue(
                "error",
                "lessons",
                f"{lesson_path}.cognitive_load",
                f"must be one of {', '.join(sorted(VALID_COGNITIVE_LOADS))}",
            )
        )

    type_value = require_mapping(issues, lesson.get("type"), f"{lesson_path}.type", "lesson-type")
    category = None
    if type_value is not None:
        add_missing_key_errors(
            issues, type_value, LESSON_TYPE_KEYS, f"{lesson_path}.type", "lesson-type"
        )
        warn_extra_keys(
            issues, type_value, LESSON_TYPE_KEYS, f"{lesson_path}.type", "schema-hygiene"
        )
        category = type_value.get("category")
        medium = type_value.get("medium")
        interaction = type_value.get("interaction")
        if category not in VALID_TYPE_CATEGORIES:
            issues.append(
                issue("error", "lesson-type", f"{lesson_path}.type.category", "invalid category")
            )
        if medium not in VALID_TYPE_MEDIA:
            issues.append(
                issue("error", "lesson-type", f"{lesson_path}.type.medium", "invalid medium")
            )
        if interaction not in VALID_TYPE_INTERACTIONS:
            issues.append(
                issue(
                    "error", "lesson-type", f"{lesson_path}.type.interaction", "invalid interaction"
                )
            )
        if category == "project":
            stats.projects += 1
        if category == "checkpoint":
            stats.checkpoints += 1

    validate_source_reference(
        issues,
        lesson,
        lesson_path,
        source_ids,
        source_roots,
        manifest_path,
        category if isinstance(category, str) else None,
        options,
    )
    return minutes_value


def validate_source_reference(
    issues: list[ValidationIssue],
    lesson: dict[str, Any],
    lesson_path: str,
    source_ids: dict[str, str],
    source_roots: dict[str, Path],
    manifest_path: Path,
    type_category: str | None,
    options: ValidationOptions,
) -> None:
    source = require_mapping(
        issues, lesson.get("source"), f"{lesson_path}.source", "source-references"
    )
    if source is None:
        return
    warn_extra_keys(issues, source, SOURCE_REF_KEYS, f"{lesson_path}.source", "schema-hygiene")
    inferred = lesson.get("inferred") is True
    repo = source.get("repo")
    source_path = source.get("path")

    if inferred:
        if type_category not in {"project", "checkpoint"}:
            issues.append(
                issue(
                    "warning",
                    "source-references",
                    f"{lesson_path}.type.category",
                    "inferred lessons should normally be project or checkpoint entries",
                )
            )
        if (
            not options.allow_missing_source_files
            and repo not in {None, "manifest"}
            and not (
                isinstance(repo, str)
                and repo in source_ids
                and isinstance(source_path, str)
                and (source_roots[repo] / source_path).exists()
            )
        ):
            issues.append(
                issue(
                    "warning",
                    "source-references",
                    f"{lesson_path}.source",
                    "inferred lesson references a source repo without a valid path",
                )
            )
        return

    if repo not in source_ids:
        issues.append(
            issue(
                "error",
                "source-references",
                f"{lesson_path}.source.repo",
                "must reference a known source id",
            )
        )
        return
    if not isinstance(source_path, str) or not source_path.strip():
        issues.append(
            issue(
                "error",
                "source-references",
                f"{lesson_path}.source.path",
                "must be a non-empty string",
            )
        )
        return
    resolved = resolve_source_path(source, source_roots, manifest_path)
    if resolved is None:
        issues.append(
            issue(
                "error",
                "source-references",
                f"{lesson_path}.source.path",
                "could not resolve source path",
            )
        )
        return
    source_root = source_roots[repo]
    try:
        resolved.resolve().relative_to(source_root.resolve())
    except ValueError:
        issues.append(
            issue(
                "error",
                "source-references",
                f"{lesson_path}.source.path",
                "must stay inside the declared source repository",
            )
        )
    if not resolved.exists():
        if not options.allow_missing_source_files:
            issues.append(
                issue(
                    "error",
                    "source-references",
                    f"{lesson_path}.source.path",
                    "source path does not exist",
                )
            )
        return
    heading = source.get("heading")
    if heading is not None:
        if not is_non_empty_string(heading):
            issues.append(
                issue(
                    "error",
                    "source-references",
                    f"{lesson_path}.source.heading",
                    "must be a non-empty string",
                )
            )
        elif resolved.suffix.lower() in {".md", ".markdown"}:
            headings = markdown_headings(resolved)
            if normalize_heading(heading) not in headings:
                issues.append(
                    issue(
                        "warning",
                        "source-references",
                        f"{lesson_path}.source.heading",
                        "heading was not found in markdown source file",
                    )
                )


def warn_later_module_dependencies(
    issues: list[ValidationIssue],
    track_id: Any,
    module_id: Any,
    deps: Any,
    module_order_by_track: dict[str, dict[str, int]],
    path: str,
) -> None:
    if (
        not isinstance(track_id, str)
        or not isinstance(module_id, str)
        or not isinstance(deps, list)
    ):
        return
    orders = module_order_by_track.get(track_id, {})
    current_order = orders.get(module_id)
    if current_order is None:
        return
    for index, dep in enumerate(deps):
        dep_order = orders.get(dep)
        if dep_order is not None and dep_order > current_order:
            issues.append(
                issue(
                    "warning",
                    "dependency-graph",
                    f"{path}[{index}]",
                    "module depends on a later module in the same track",
                )
            )


def warn_later_lesson_dependencies(
    issues: list[ValidationIssue],
    module_id: Any,
    lesson_id: Any,
    deps: Any,
    lesson_order_by_module: dict[str, dict[str, int]],
    path: str,
) -> None:
    if (
        not isinstance(module_id, str)
        or not isinstance(lesson_id, str)
        or not isinstance(deps, list)
    ):
        return
    orders = lesson_order_by_module.get(module_id, {})
    current_order = orders.get(lesson_id)
    if current_order is None:
        return
    for index, dep in enumerate(deps):
        dep_order = orders.get(dep)
        if dep_order is not None and dep_order > current_order:
            issues.append(
                issue(
                    "warning",
                    "dependency-graph",
                    f"{path}[{index}]",
                    "lesson depends on a later lesson in the same module",
                )
            )


def print_grouped(title: str, issues: list[ValidationIssue]) -> None:
    if not issues:
        return
    print(f"\n{title}:")
    by_category: dict[str, list[ValidationIssue]] = defaultdict(list)
    for validation_issue in issues:
        by_category[validation_issue.category].append(validation_issue)
    for category in sorted(by_category):
        print(f"  {category}:")
        for validation_issue in by_category[category]:
            print(f"    - {validation_issue.path}: {validation_issue.message}")


def print_summary(manifest_path: Path, result: ValidationResult) -> None:
    errors = result.errors
    warnings = result.warnings
    print("Validation passed." if not errors else "Validation failed.")
    print()
    print(
        f"Manifest: {manifest_path.relative_to(REPO_ROOT) if manifest_path.is_absolute() else manifest_path}"
    )
    print(f"Schema version: {result.schema_version}")
    print(f"Sources: {result.stats.sources}")
    print(f"Tracks: {result.stats.tracks}")
    print(f"Modules: {result.stats.modules}")
    print(f"Lessons: {result.stats.lessons}")
    print(f"Projects: {result.stats.projects}")
    print(f"Checkpoints: {result.stats.checkpoints}")
    print(
        f"Estimated effort: {result.stats.estimated_hours:.1f} hours ({result.stats.estimated_minutes} minutes)"
    )
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    print_grouped("Errors", errors)
    print_grouped("Warnings", warnings)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate an OpenStudy curriculum manifest.")
    parser.add_argument(
        "manifest",
        nargs="?",
        default=str(DEFAULT_MANIFEST),
        help=f"Manifest path to validate (default: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--allow-missing-source-files",
        action="store_true",
        help=(
            "Downgrade missing curriculum source files/assets to warnings. "
            "Use this for lean production images that include the generated manifest "
            "but intentionally exclude curriculum/sources."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path
    data, load_exit = load_manifest(manifest_path)
    if data is None:
        return load_exit
    result = validate_manifest(
        data,
        manifest_path,
        ValidationOptions(
            allow_missing_source_files=(
                args.allow_missing_source_files
                or os.environ.get("OPENSTUDY_PACKAGED_CURRICULUM") == "1"
            )
        ),
    )
    print_summary(manifest_path, result)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
