"""Create a deterministic schema v2 curriculum manifest from local sources.

Run from the repo root:
    python scripts/curriculum/create_manifest.py --force

Use --dry-run to print the generated YAML without writing the output file.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES_ROOT = Path("curriculum/sources")
DEFAULT_OUTPUT = Path("curriculum/interview_manifest.v2.yaml")
DEFAULT_BASE = Path("curriculum/manifests/interview-engineering.yaml")
MANIFEST_SOURCE_PATH = "curriculum/interview_manifest.v2.yaml"


@dataclass(frozen=True)
class SourceConfig:
    name: str
    upstream: str
    course_code: str
    prefix: str
    kind: str


SOURCE_CONFIG: dict[str, SourceConfig] = {
    "coding-interview-university": SourceConfig(
        name="Coding Interview University",
        upstream="https://github.com/jwasham/coding-interview-university",
        course_code="CIU",
        prefix="ciu",
        kind="core-curriculum",
    ),
    "computer-science-flash-cards": SourceConfig(
        name="Computer Science Flash Cards",
        upstream="https://github.com/jwasham/computer-science-flash-cards",
        course_code="CIU",
        prefix="ciu",
        kind="flashcards",
    ),
    "interactive-coding-challenges": SourceConfig(
        name="Interactive Coding Challenges",
        upstream="https://github.com/donnemartin/interactive-coding-challenges",
        course_code="ICC",
        prefix="icc",
        kind="coding-practice",
    ),
    "practice-c": SourceConfig(
        name="Practice C",
        upstream="https://github.com/jwasham/practice-c",
        course_code="PC",
        prefix="pc",
        kind="implementation-practice",
    ),
    "practice-cpp": SourceConfig(
        name="Practice C++",
        upstream="https://github.com/jwasham/practice-cpp",
        course_code="PCPP",
        prefix="pcpp",
        kind="implementation-practice",
    ),
    "practice-python": SourceConfig(
        name="Practice Python",
        upstream="https://github.com/jwasham/practice-python",
        course_code="PY",
        prefix="py",
        kind="implementation-practice",
    ),
    "system-design-primer": SourceConfig(
        name="System Design Primer",
        upstream="https://github.com/donnemartin/system-design-primer",
        course_code="SDP",
        prefix="sdp",
        kind="system-design",
    ),
}


def kebab_case(value: str) -> str:
    normalized = value.replace("+", " plus ")
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").lower()
    normalized = re.sub(r"-+", "-", normalized)
    return normalized or "item"


def relative_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def source_relative(source_root: Path, path: Path) -> str:
    return path.resolve().relative_to(source_root.resolve()).as_posix()


def source_dirs(sources_root: Path) -> list[Path]:
    if not sources_root.exists():
        raise FileNotFoundError(f"sources root does not exist: {sources_root}")
    if not sources_root.is_dir():
        raise NotADirectoryError(f"sources root is not a directory: {sources_root}")
    return sorted(
        (path for path in sources_root.iterdir() if path.is_dir()), key=lambda path: path.name
    )


def readable_title_from_slug(value: str) -> str:
    replacements = {"cpp": "C++", "c": "C", "python": "Python"}
    words = value.replace("_", "-").split("-")
    return " ".join(replacements.get(word, word.capitalize()) for word in words)


def lesson_type(category: str, medium: str, interaction: str) -> dict[str, str]:
    return {"category": category, "medium": medium, "interaction": interaction}


def difficulty(level: str, score: int) -> dict[str, int | str]:
    return {"level": level, "score": score}


def source_ref(repo: str, path: str, heading: str | None = None) -> dict[str, str]:
    ref = {"repo": repo, "path": path}
    if heading:
        ref["heading"] = heading
    return ref


def manifest_ref() -> dict[str, str]:
    return {"repo": "manifest", "path": MANIFEST_SOURCE_PATH}


def lesson(
    lesson_id: str,
    title: str,
    category: str,
    medium: str,
    interaction: str,
    source: dict[str, str],
    minutes: int,
    criteria: str,
    order: int,
    depends_on: list[str],
    level: str,
    score: int,
    cognitive_load: str,
    *,
    inferred: bool = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": lesson_id,
        "title": title,
        "type": lesson_type(category, medium, interaction),
        "source": source,
        "estimated_minutes": minutes,
        "completion_criteria": criteria,
        "suggested_order": order,
        "depends_on": depends_on,
        "difficulty": difficulty(level, score),
        "cognitive_load": cognitive_load,
        "mastery_state": "not_started",
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
    if inferred:
        data["inferred"] = True
    return data


def effort_band(minutes: int) -> str:
    if minutes <= 90:
        return "light"
    if minutes <= 150:
        return "standard"
    if minutes <= 300:
        return "deep"
    return "multi-session"


def retry_density(lessons: list[dict[str, Any]]) -> str:
    practice_count = sum(
        1
        for item in lessons
        if isinstance(item.get("type"), dict)
        and item["type"].get("category") in {"practice", "checkpoint", "project"}
    )
    if practice_count >= 5:
        return "high"
    if practice_count >= 2:
        return "medium"
    return "low"


def decay_risk_for(tags: list[str], cognitive_load: str) -> str:
    high_decay_tags = {"dynamic-programming", "graphs", "recursion", "system-design", "caching"}
    if cognitive_load == "high" or high_decay_tags.intersection(tags):
        return "high"
    if cognitive_load == "medium":
        return "medium"
    return "low"


def finalize_module(
    module_id: str,
    name: str,
    description: str,
    level: str,
    score: int,
    tags: list[str],
    prerequisites: list[str],
    depends_on: list[str],
    order: int,
    lessons: list[dict[str, Any]],
) -> dict[str, Any]:
    minutes = sum(int(item["estimated_minutes"]) for item in lessons)
    max_load = "low"
    if any(item.get("cognitive_load") == "high" for item in lessons):
        max_load = "high"
    elif any(item.get("cognitive_load") == "medium" for item in lessons):
        max_load = "medium"
    return {
        "id": module_id,
        "name": name,
        "description": description,
        "difficulty": difficulty(level, score),
        "estimated_effort_band": effort_band(minutes),
        "expected_retry_density": retry_density(lessons),
        "cognitive_load": max_load,
        "decay_risk": decay_risk_for(tags, max_load),
        "interview_frequency": "high" if score >= 2 else "medium",
        "current_mastery_state": "not_started",
        "tags": tags,
        "prerequisites": prerequisites,
        "depends_on": depends_on,
        "suggested_order": order,
        "lessons": lessons,
    }


def load_base_manifest(base_path: Path) -> dict[str, Any]:
    if not base_path.exists():
        raise FileNotFoundError(f"base manifest does not exist: {base_path}")
    if not base_path.is_file():
        raise FileNotFoundError(f"base manifest is not a file: {base_path}")
    with base_path.open("r", encoding="utf-8") as manifest_file:
        data = yaml.safe_load(manifest_file)
    if not isinstance(data, dict):
        raise ValueError(f"base manifest must be a YAML mapping: {base_path}")
    return data


def existing_readme(source_root: Path) -> str | None:
    readme = source_root / "README.md"
    if readme.exists():
        return "README.md"
    candidates = sorted(source_root.glob("README*"))
    if not candidates:
        return None
    return source_relative(source_root, candidates[0])


def discover_source_metadata(sources_root: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for path in source_dirs(sources_root):
        config = SOURCE_CONFIG.get(path.name)
        name = config.name if config else readable_title_from_slug(path.name)
        source: dict[str, Any] = {
            "id": path.name,
            "name": name,
            "path": relative_to_repo(path),
            "upstream": config.upstream if config else f"https://github.com/{path.name}",
        }
        if config:
            source["kind"] = config.kind
            source["course_code"] = config.course_code
        sources.append(source)
    return sources


def asset_for_path(source_id: str, source_root: Path, asset_path: Path) -> dict[str, Any] | None:
    suffix = asset_path.suffix.lower().removeprefix(".")
    relative_path = source_relative(source_root, asset_path)
    filename = asset_path.name
    stem_id = kebab_case(asset_path.stem)

    if source_id == "computer-science-flash-cards" and relative_path == "cards-empty.db":
        return None

    if source_id == "interactive-coding-challenges" and relative_path == "anki_cards/Coding.apkg":
        return {
            "id": "icc-anki-coding",
            "title": "Interactive Coding Challenges Coding Anki Deck",
            "kind": "flashcard-deck",
            "format": "apkg",
            "course_code": "ICC",
            "source": source_ref(source_id, relative_path),
            "storage_path": "ICC/flashcards/Coding.apkg",
            "usage": {
                "openstudy_readable": False,
                "import_into_anki": True,
                "review_cadence": "spaced",
            },
        }

    if source_id == "computer-science-flash-cards" and relative_path == "cards-jwasham.db":
        return {
            "id": "ciu-flashcards-standard",
            "title": "Computer Science Flash Cards",
            "kind": "flashcard-database",
            "format": "db",
            "course_code": "CIU",
            "source": source_ref(source_id, relative_path),
            "storage_path": "CIU/flashcards/cards-jwasham.db",
            "usage": {
                "openstudy_readable": False,
                "import_into_anki": False,
            },
        }

    if source_id == "system-design-primer" and relative_path.startswith("resources/flash_cards/"):
        return {
            "id": f"sdp-flashcards-{stem_id}",
            "title": f"System Design Primer {asset_path.stem} Anki Deck",
            "kind": "flashcard-deck",
            "format": "apkg",
            "course_code": "SDP",
            "source": source_ref(source_id, relative_path),
            "storage_path": f"SDP/flashcards/{filename}",
            "usage": {
                "openstudy_readable": False,
                "import_into_anki": True,
                "review_cadence": "spaced",
            },
        }

    config = SOURCE_CONFIG.get(source_id)
    if suffix == "apkg":
        course_code = config.course_code if config else source_id.upper()
        return {
            "id": f"{config.prefix if config else kebab_case(source_id)}-flashcards-{stem_id}",
            "title": f"{config.name if config else readable_title_from_slug(source_id)} {asset_path.stem} Anki Deck",
            "kind": "flashcard-deck",
            "format": "apkg",
            "course_code": course_code,
            "source": source_ref(source_id, relative_path),
            "storage_path": f"{course_code}/flashcards/{filename}",
            "usage": {
                "openstudy_readable": False,
                "import_into_anki": True,
                "review_cadence": "spaced",
            },
        }
    if suffix == "db":
        course_code = config.course_code if config else source_id.upper()
        return {
            "id": f"{config.prefix if config else kebab_case(source_id)}-flashcards-{stem_id}",
            "title": f"{config.name if config else readable_title_from_slug(source_id)} {asset_path.stem} Database",
            "kind": "flashcard-database",
            "format": "db",
            "course_code": course_code,
            "source": source_ref(source_id, relative_path),
            "storage_path": f"{course_code}/flashcards/{filename}",
            "usage": {
                "openstudy_readable": False,
                "import_into_anki": False,
            },
        }
    return None


def discover_assets(sources_root: Path) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for source_root in source_dirs(sources_root):
        candidates = sorted(
            [
                *source_root.rglob("*.apkg"),
                *source_root.rglob("*.db"),
            ],
            key=lambda path: source_relative(source_root, path),
        )
        for asset_path in candidates:
            asset = asset_for_path(source_root.name, source_root, asset_path)
            if asset is not None:
                assets.append(asset)
    return sorted(assets, key=lambda item: item["id"])


def has_source(sources_root: Path, source_id: str) -> bool:
    return (sources_root / source_id).is_dir()


def add_module(modules: list[dict[str, Any]], module: dict[str, Any]) -> None:
    modules.append(module)


def build_core_modules(sources_root: Path) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []

    if has_source(sources_root, "coding-interview-university"):
        lessons = [
            lesson(
                "ciu-study-plan-orientation",
                "Orient to the interview study plan",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                60,
                "Record the study scope, adaptive cadence, and personal constraints.",
                1,
                [],
                "beginner",
                1,
                "low",
            ),
            lesson(
                "ciu-big-o-and-complexity",
                "Study Big-O and complexity analysis",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                120,
                "Explain time and space complexity for common loops, recursion, and data structure operations.",
                2,
                ["ciu-study-plan-orientation"],
                "beginner",
                2,
                "medium",
            ),
            lesson(
                "ciu-interview-language-setup",
                "Choose and configure the primary interview language",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                90,
                "Document the language, editor, test runner, and debugging workflow to use for practice.",
                3,
                ["ciu-big-o-and-complexity"],
                "beginner",
                2,
                "medium",
            ),
        ]
        add_module(
            modules,
            finalize_module(
                "ie-module-foundations-and-complexity",
                "Foundations and Complexity",
                "Establish the study workflow, programming environment, and asymptotic analysis baseline.",
                "beginner",
                2,
                ["interview-prep", "study-skills", "complexity"],
                [],
                [],
                len(modules) + 1,
                lessons,
            ),
        )

        lessons = [
            lesson(
                "ciu-arrays-and-strings",
                "Review arrays, strings, and contiguous memory trade-offs",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                120,
                "Summarize operations, complexity, and common interview patterns for arrays and strings.",
                1,
                ["ciu-interview-language-setup"],
                "beginner",
                3,
                "medium",
            ),
            lesson(
                "ciu-linked-lists-stacks-queues",
                "Review linked lists, stacks, and queues",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                150,
                "Implement or trace core operations and describe when each structure is appropriate.",
                2,
                ["ciu-arrays-and-strings"],
                "beginner",
                3,
                "medium",
            ),
            lesson(
                "ciu-hash-tables-and-sets",
                "Review hash tables and set-based lookup",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                120,
                "Explain collisions, load factor, lookup complexity, and common interview uses.",
                3,
                ["ciu-linked-lists-stacks-queues"],
                "intermediate",
                4,
                "medium",
            ),
        ]
        add_module(
            modules,
            finalize_module(
                "ie-module-core-data-structures",
                "Core Data Structures",
                "Normalize the essential data structures used in algorithm interviews.",
                "beginner",
                3,
                ["data-structures", "arrays", "hashing"],
                ["ie-module-foundations-and-complexity"],
                ["ie-module-foundations-and-complexity"],
                len(modules) + 1,
                lessons,
            ),
        )

        lessons = [
            lesson(
                "ciu-searching-and-sorting",
                "Review searching and sorting strategies",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                150,
                "Compare binary search, comparison sorts, non-comparison sorts, and stability trade-offs.",
                1,
                ["ciu-hash-tables-and-sets"],
                "intermediate",
                4,
                "medium",
            ),
            lesson(
                "ciu-recursion-and-backtracking",
                "Review recursion and backtracking patterns",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                150,
                "Trace recursive calls, base cases, stack depth, and backtracking state restoration.",
                2,
                ["ciu-searching-and-sorting"],
                "intermediate",
                5,
                "high",
            ),
            lesson(
                "ciu-dynamic-programming",
                "Review dynamic programming",
                "reading",
                "markdown",
                "passive",
                source_ref("coding-interview-university", "README.md"),
                180,
                "Define state, transition, base cases, and memoization or tabulation for representative problems.",
                3,
                ["ciu-recursion-and-backtracking"],
                "advanced",
                6,
                "high",
            ),
        ]
        add_module(
            modules,
            finalize_module(
                "ie-module-algorithm-patterns",
                "Algorithm Patterns",
                "Cover the recurring algorithmic techniques that drive coding interview problems.",
                "intermediate",
                5,
                ["algorithms", "sorting", "recursion", "dynamic-programming"],
                ["ie-module-core-data-structures"],
                ["ie-module-core-data-structures"],
                len(modules) + 1,
                lessons,
            ),
        )

    if has_source(sources_root, "interactive-coding-challenges"):
        icc_root = sources_root / "interactive-coding-challenges"
        challenge_groups = [
            (
                "arrays_strings",
                "icc-arrays-strings-practice",
                "Arrays and strings challenge practice",
            ),
            ("linked_lists", "icc-linked-lists-practice", "Linked lists challenge practice"),
            ("stacks_queues", "icc-stacks-queues-practice", "Stacks and queues challenge practice"),
            ("graphs_trees", "icc-graphs-trees-practice", "Graphs and trees challenge practice"),
            (
                "sorting_searching",
                "icc-sorting-searching-practice",
                "Sorting and searching challenge practice",
            ),
            (
                "recursion_dynamic",
                "icc-recursion-dynamic-practice",
                "Recursion and dynamic programming challenge practice",
            ),
        ]
        lessons = []
        previous = (
            "ciu-dynamic-programming"
            if has_source(sources_root, "coding-interview-university")
            else None
        )
        for index, (directory, lesson_id, title) in enumerate(challenge_groups, start=1):
            path = directory if (icc_root / directory).exists() else "README.md"
            lessons.append(
                lesson(
                    lesson_id,
                    title,
                    "coding-exercise",
                    "notebook" if path != "README.md" else "markdown",
                    "active",
                    source_ref("interactive-coding-challenges", path),
                    180,
                    "Complete representative challenge notebooks and review tested reference solutions.",
                    index,
                    [previous] if previous else [],
                    "intermediate",
                    5 if index < 4 else 6,
                    "high",
                )
            )
            previous = lesson_id
        add_module(
            modules,
            finalize_module(
                "ie-module-coding-challenge-practice",
                "Coding Challenge Practice",
                "Apply the foundation through curated challenge categories without expanding every notebook.",
                "intermediate",
                5,
                ["coding-exercises", "notebooks", "practice"],
                ["ie-module-algorithm-patterns"]
                if has_source(sources_root, "coding-interview-university")
                else [],
                ["ie-module-algorithm-patterns"]
                if has_source(sources_root, "coding-interview-university")
                else [],
                len(modules) + 1,
                lessons,
            ),
        )

    if has_source(sources_root, "system-design-primer"):
        lessons = [
            lesson(
                "sdp-system-design-orientation",
                "Orient to system design interviews",
                "reading",
                "markdown",
                "passive",
                source_ref("system-design-primer", "README.md"),
                90,
                "Summarize the interview approach, problem framing steps, and evaluation criteria.",
                1,
                ["icc-recursion-dynamic-practice"]
                if has_source(sources_root, "interactive-coding-challenges")
                else [],
                "intermediate",
                4,
                "medium",
            ),
            lesson(
                "sdp-scalability-fundamentals",
                "Study scalability, latency, throughput, and capacity",
                "reading",
                "markdown",
                "passive",
                source_ref("system-design-primer", "README.md"),
                150,
                "Explain the difference between performance and scalability with concrete system examples.",
                2,
                ["sdp-system-design-orientation"],
                "intermediate",
                5,
                "high",
            ),
            lesson(
                "sdp-consistency-and-availability",
                "Study consistency, availability, and CAP trade-offs",
                "reading",
                "markdown",
                "passive",
                source_ref("system-design-primer", "README.md"),
                150,
                "Compare consistency and availability patterns and identify trade-offs in example designs.",
                3,
                ["sdp-scalability-fundamentals"],
                "advanced",
                6,
                "high",
            ),
        ]
        add_module(
            modules,
            finalize_module(
                "ie-module-system-design-fundamentals",
                "System Design Fundamentals",
                "Build the vocabulary and trade-off model for system design interviews.",
                "intermediate",
                5,
                ["system-design", "scalability", "trade-offs"],
                ["ie-module-coding-challenge-practice"]
                if has_source(sources_root, "interactive-coding-challenges")
                else [],
                ["ie-module-coding-challenge-practice"]
                if has_source(sources_root, "interactive-coding-challenges")
                else [],
                len(modules) + 1,
                lessons,
            ),
        )

        lessons = [
            lesson(
                "sdp-databases-and-storage",
                "Study databases and storage trade-offs",
                "reading",
                "markdown",
                "passive",
                source_ref("system-design-primer", "README.md"),
                150,
                "Compare relational, NoSQL, replication, sharding, and denormalization trade-offs.",
                1,
                ["sdp-consistency-and-availability"],
                "advanced",
                6,
                "high",
            ),
            lesson(
                "sdp-caching-and-content-delivery",
                "Study caching and content delivery",
                "reading",
                "markdown",
                "passive",
                source_ref("system-design-primer", "README.md"),
                120,
                "Explain cache placement, invalidation options, CDN behavior, and update strategies.",
                2,
                ["sdp-databases-and-storage"],
                "advanced",
                6,
                "high",
            ),
            lesson(
                "sdp-load-balancing-and-queues",
                "Study load balancing, queues, and asynchronous processing",
                "reading",
                "markdown",
                "passive",
                source_ref("system-design-primer", "README.md"),
                150,
                "Diagram request flow through load balancers, services, caches, queues, and workers.",
                3,
                ["sdp-caching-and-content-delivery"],
                "advanced",
                7,
                "high",
            ),
        ]
        add_module(
            modules,
            finalize_module(
                "ie-module-distributed-systems-components",
                "Distributed Systems Components",
                "Study the storage, caching, routing, and asynchronous building blocks used in scalable designs.",
                "advanced",
                6,
                ["distributed-systems", "databases", "caching", "queues"],
                ["ie-module-system-design-fundamentals"],
                ["ie-module-system-design-fundamentals"],
                len(modules) + 1,
                lessons,
            ),
        )

        solution_readmes = sorted(
            (sources_root / "system-design-primer" / "solutions" / "system_design").glob(
                "*/README.md"
            )
        )
        practice_source = (
            source_relative(sources_root / "system-design-primer", solution_readmes[0])
            if solution_readmes
            else "README.md"
        )
        lessons = [
            lesson(
                "sdp-system-design-case-study",
                "Review a complete system design case study",
                "reading",
                "markdown",
                "passive",
                source_ref("system-design-primer", practice_source),
                180,
                "Extract requirements, APIs, data model, scaling strategy, and major trade-offs from one solution.",
                1,
                ["sdp-load-balancing-and-queues"],
                "advanced",
                7,
                "high",
            ),
            lesson(
                "ie-system-design-mock",
                "Run a timed system design mock",
                "checkpoint",
                "manifest",
                "reflective",
                manifest_ref(),
                120,
                "Complete a 45-minute design prompt and write a structured retrospective.",
                2,
                ["sdp-system-design-case-study"],
                "advanced",
                7,
                "high",
                inferred=True,
            ),
        ]
        add_module(
            modules,
            finalize_module(
                "ie-module-system-design-interview-practice",
                "System Design Interview Practice",
                "Use sourced case studies and timed checkpoints to rehearse the interview format.",
                "advanced",
                7,
                ["system-design", "mock-interviews", "case-studies"],
                ["ie-module-distributed-systems-components"],
                ["ie-module-distributed-systems-components"],
                len(modules) + 1,
                lessons,
            ),
        )

    if has_source(sources_root, "computer-science-flash-cards"):
        lessons = [
            lesson(
                "ciu-flashcard-deck-orientation",
                "Review available computer science flashcard assets",
                "reading",
                "markdown",
                "passive",
                source_ref("computer-science-flash-cards", "README.md"),
                45,
                "Identify which decks support interview review and how they should be imported or scheduled.",
                1,
                [],
                "beginner",
                2,
                "low",
            ),
            lesson(
                "ie-weekly-flashcard-review",
                "Schedule spaced flashcard review",
                "checkpoint",
                "manifest",
                "reflective",
                manifest_ref(),
                45,
                "Complete a spaced review pass and note weak topics for the next agenda.",
                2,
                ["ciu-flashcard-deck-orientation"],
                "beginner",
                2,
                "low",
                inferred=True,
            ),
        ]
        add_module(
            modules,
            finalize_module(
                "ie-module-spaced-repetition-review",
                "Spaced Repetition Review",
                "Track non-readable flashcard assets as supplemental review material.",
                "beginner",
                2,
                ["flashcards", "review", "spaced-repetition"],
                [],
                [],
                len(modules) + 1,
                lessons,
            ),
        )

    return modules


def discovered_resource_summary(source_root: Path) -> str:
    directories = sorted(
        path.name
        for path in source_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    if not directories:
        return "README and repository resources"
    return ", ".join(directories[:8])


def build_practice_module(source_id: str, source_root: Path, order: int) -> dict[str, Any]:
    config = SOURCE_CONFIG[source_id]
    readme = existing_readme(source_root) or "."
    prefix = config.prefix
    setup_id = f"{prefix}-implementation-practice-orientation"
    project_id = f"ie-{kebab_case(source_id)}-implementation-drill"
    summary = discovered_resource_summary(source_root)
    lessons = [
        lesson(
            setup_id,
            f"Orient to {config.name} implementation practice",
            "reading",
            "markdown" if readme.endswith(".md") else "reference",
            "passive",
            source_ref(source_id, readme),
            45,
            f"Identify available practice areas and choose one data structure or algorithm to implement. Discovered areas: {summary}.",
            1,
            [],
            "intermediate",
            4,
            "medium",
        ),
        lesson(
            project_id,
            f"Complete a {config.name} implementation drill",
            "project",
            "manifest",
            "constructive",
            manifest_ref(),
            150,
            "Implement, run, and briefly document one focused data structure or algorithm exercise in this language.",
            2,
            [setup_id],
            "intermediate",
            5,
            "high",
            inferred=True,
        ),
    ]
    return finalize_module(
        f"ie-module-{kebab_case(source_id)}",
        f"{config.name} Implementation Practice",
        "Use the supplemental practice repository as a compact implementation drill rather than a large task expansion.",
        "intermediate",
        4,
        ["implementation-practice", kebab_case(config.name), "supplemental"],
        [],
        [],
        order,
        lessons,
    )


def build_tracks(sources_root: Path) -> list[dict[str, Any]]:
    modules = build_core_modules(sources_root)
    for source_id in ("practice-c", "practice-cpp", "practice-python"):
        source_root = sources_root / source_id
        if source_root.is_dir():
            modules.append(build_practice_module(source_id, source_root, len(modules) + 1))

    if modules:
        last_lesson_id = modules[-1]["lessons"][-1]["id"]
    else:
        last_lesson_id = None
    capstone_lessons = [
        lesson(
            "ie-capstone-readiness-review",
            "Complete the capstone readiness review",
            "checkpoint",
            "manifest",
            "reflective",
            manifest_ref(),
            90,
            "Review weak areas, unresolved topics, flashcard performance, and mock interview notes.",
            1,
            [last_lesson_id] if last_lesson_id else [],
            "advanced",
            7,
            "medium",
            inferred=True,
        ),
        lesson(
            "ie-capstone-mock-interview-loop",
            "Run a full mock interview loop",
            "project",
            "manifest",
            "constructive",
            manifest_ref(),
            180,
            "Complete one coding mock, one system design mock, and one retrospective with next actions.",
            2,
            ["ie-capstone-readiness-review"],
            "advanced",
            8,
            "high",
            inferred=True,
        ),
    ]
    modules.append(
        finalize_module(
            "ie-module-capstone-and-mock-interviews",
            "Capstone and Mock Interviews",
            "Close the track with readiness review, realistic mock interviews, and targeted remediation.",
            "advanced",
            8,
            ["capstone", "mock-interviews", "review"],
            [modules[-1]["id"]] if modules else [],
            [modules[-1]["id"]] if modules else [],
            len(modules) + 1,
            capstone_lessons,
        )
    )

    return [
        {
            "id": "ie-track-core",
            "name": "Interview Engineering Core",
            "description": "Deterministic v2 study plan generated from local curriculum source repositories.",
            "audience": "personal software engineering interview preparation",
            "suggested_order": 1,
            "depends_on": [],
            "modules": modules,
        }
    ]


def lesson_minutes(data: dict[str, Any]) -> int:
    return sum(
        int(lesson["estimated_minutes"])
        for track in data.get("tracks", [])
        for module in track.get("modules", [])
        for lesson in module.get("lessons", [])
        if isinstance(lesson, dict) and isinstance(lesson.get("estimated_minutes"), int)
    )


def module_lesson_minutes(module: dict[str, Any]) -> int:
    lessons = module.get("lessons", [])
    if not isinstance(lessons, list):
        return 0
    return sum(
        int(lesson["estimated_minutes"])
        for lesson in lessons
        if isinstance(lesson, dict) and isinstance(lesson.get("estimated_minutes"), int)
    )


def recalculate_module_effort_bands(data: dict[str, Any]) -> None:
    for track in data.get("tracks", []):
        if not isinstance(track, dict):
            continue
        modules = track.get("modules", [])
        if not isinstance(modules, list):
            continue
        for module in modules:
            if not isinstance(module, dict):
                continue
            minutes = module_lesson_minutes(module)
            if minutes > 0:
                module.pop("estimated_hours", None)
                module["estimated_effort_band"] = effort_band(minutes)
                lessons = module.get("lessons", [])
                if isinstance(lessons, list):
                    module["expected_retry_density"] = retry_density(lessons)
                    for lesson_item in lessons:
                        if isinstance(lesson_item, dict):
                            lesson_item.setdefault("mastery_state", "not_started")
                            lesson_item.setdefault(
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
                            )
                module.setdefault("cognitive_load", "medium")
                module.setdefault("decay_risk", decay_risk_for(module.get("tags", []), module["cognitive_load"]))
                module.setdefault("interview_frequency", "medium")
                module.setdefault("current_mastery_state", "not_started")


def module_ids(data: dict[str, Any]) -> set[str]:
    return {
        module["id"]
        for track in data.get("tracks", [])
        for module in track.get("modules", [])
        if isinstance(module, dict) and isinstance(module.get("id"), str)
    }


def enrich_base_manifest(base_data: dict[str, Any], sources_root: Path) -> dict[str, Any]:
    resolved_sources_root = sources_root if sources_root.is_absolute() else REPO_ROOT / sources_root
    data = copy.deepcopy(base_data)
    data["sources"] = discover_source_metadata(resolved_sources_root)
    data["assets"] = discover_assets(resolved_sources_root)

    tracks = data.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise ValueError("base manifest must contain a non-empty tracks list")

    existing_module_ids = module_ids(data)
    primary_track = tracks[0]
    modules = primary_track.get("modules")
    if not isinstance(modules, list):
        raise ValueError("base manifest first track must contain a modules list")

    for source_id in ("practice-c", "practice-cpp", "practice-python"):
        source_root = resolved_sources_root / source_id
        module_id = f"ie-module-{kebab_case(source_id)}"
        if source_root.is_dir() and module_id not in existing_module_ids:
            modules.append(build_practice_module(source_id, source_root, len(modules) + 1))
            existing_module_ids.add(module_id)

    recalculate_module_effort_bands(data)

    meta = data.setdefault("meta", {})
    if not isinstance(meta, dict):
        raise ValueError("base manifest meta must be a mapping")
    meta["schema_version"] = 2
    meta.pop("estimated_total_hours", None)
    meta["effort_model"] = {
        "kind": "elastic",
        "bands": {
            "light": "45-90 minutes",
            "standard": "90-150 minutes",
            "deep": "3-5 hours",
            "multi-session": "requires repeated sessions and retries",
        },
        "agenda_is_source_of_truth": True,
    }
    id_policy = meta.setdefault("id_policy", {})
    if isinstance(id_policy, dict):
        prefixes = id_policy.setdefault("lesson_prefixes", {})
        if isinstance(prefixes, dict):
            prefixes.setdefault("pc", "Practice C sourced lesson")
            prefixes.setdefault("pcpp", "Practice C++ sourced lesson")
            prefixes.setdefault("py", "Practice Python sourced lesson")

    return data


def build_compact_manifest(sources_root: Path) -> dict[str, Any]:
    resolved_sources_root = sources_root if sources_root.is_absolute() else REPO_ROOT / sources_root
    sources = discover_source_metadata(resolved_sources_root)
    assets = discover_assets(resolved_sources_root)
    tracks = build_tracks(resolved_sources_root)
    return {
        "meta": {
            "id": "ie-curriculum-interview-engineering",
            "name": "Interview Engineering",
            "description": "Deterministic software engineering interview preparation manifest generated from local sources.",
            "timezone": "Africa/Nairobi",
            "version": 1,
            "owner": "Alex Mbugua",
            "format": "openstudy-curriculum-manifest",
            "status": "draft",
            "schema_version": 2,
            "effort_model": {
                "kind": "elastic",
                "bands": {
                    "light": "45-90 minutes",
                    "standard": "90-150 minutes",
                    "deep": "3-5 hours",
                    "multi-session": "requires repeated sessions and retries",
                },
                "agenda_is_source_of_truth": True,
            },
            "attribution_note": (
                "This generated manifest references local source repositories by path and metadata. "
                "It intentionally avoids copying large portions of upstream content."
            ),
            "id_policy": {
                "format": "stable-global-kebab-case",
                "lesson_prefixes": {
                    "ciu": "Coding Interview University or related flashcard sourced lesson",
                    "icc": "Interactive Coding Challenges sourced lesson",
                    "pc": "Practice C sourced lesson",
                    "pcpp": "Practice C++ sourced lesson",
                    "py": "Practice Python sourced lesson",
                    "sdp": "System Design Primer sourced lesson",
                    "ie": "OpenStudy inferred curriculum checkpoint or project",
                },
            },
        },
        "sources": sources,
        "assets": assets,
        "tracks": tracks,
    }


def build_manifest(sources_root: Path, base_path: Path | None = DEFAULT_BASE) -> dict[str, Any]:
    if base_path is None:
        print(
            "Warning: no base manifest provided; falling back to compact generated manifest.",
            file=sys.stderr,
        )
        return build_compact_manifest(sources_root)

    resolved_base_path = base_path if base_path.is_absolute() else REPO_ROOT / base_path
    base_data = load_base_manifest(resolved_base_path)
    return enrich_base_manifest(base_data, sources_root)


def dump_manifest(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, width=120, default_flow_style=False)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a schema v2 OpenStudy curriculum manifest."
    )
    parser.add_argument(
        "--sources-root",
        default=str(DEFAULT_SOURCES_ROOT),
        help=f"Source repositories root (default: {DEFAULT_SOURCES_ROOT})",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output manifest path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--base",
        default=str(DEFAULT_BASE),
        help=f"Curated base manifest path (default: {DEFAULT_BASE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated YAML to stdout instead of writing it.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite output if it already exists."
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print generation details to stderr."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    sources_root = Path(args.sources_root)
    output = Path(args.output)
    base = Path(args.base) if args.base else None
    if not sources_root.is_absolute():
        sources_root = REPO_ROOT / sources_root
    if not output.is_absolute():
        output = REPO_ROOT / output
    if base is not None and not base.is_absolute():
        base = REPO_ROOT / base

    try:
        data = build_manifest(sources_root, base)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"Could not create manifest: {exc}", file=sys.stderr)
        return 2

    rendered = dump_manifest(data)
    if args.dry_run:
        print(rendered, end="")
        return 0

    if output.exists() and not args.force:
        print(
            f"Output already exists: {relative_to_repo(output)}. Use --force to overwrite.",
            file=sys.stderr,
        )
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")

    if args.verbose:
        module_count = sum(len(track["modules"]) for track in data["tracks"])
        lesson_count = sum(
            len(module["lessons"]) for track in data["tracks"] for module in track["modules"]
        )
        print(
            f"Wrote {relative_to_repo(output)} with {len(data['sources'])} sources, "
            f"{module_count} modules, {lesson_count} lessons, and {len(data['assets'])} assets.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
