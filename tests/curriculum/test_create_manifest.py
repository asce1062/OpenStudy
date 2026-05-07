from __future__ import annotations

from pathlib import Path

import yaml

from scripts.curriculum.create_manifest import build_manifest


def write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_manifest_discovers_flashcards_and_compresses_practice_repos(tmp_path: Path) -> None:
    sources_root = tmp_path / "curriculum" / "sources"

    write(
        sources_root / "coding-interview-university" / "README.md",
        "# Coding Interview University\n",
    )
    write(sources_root / "system-design-primer" / "README.md", "# The System Design Primer\n")
    write(
        sources_root / "system-design-primer" / "resources" / "flash_cards" / "System Design.apkg"
    )
    write(sources_root / "interactive-coding-challenges" / "README.md", "# ICC\n")
    write(sources_root / "interactive-coding-challenges" / "anki_cards" / "Coding.apkg")
    write(sources_root / "computer-science-flash-cards" / "README.md", "# Flash Cards\n")
    write(sources_root / "computer-science-flash-cards" / "cards-jwasham.db")
    write(sources_root / "practice-c" / "README.md", "# Practice C\n")
    write(sources_root / "practice-c" / "arrays" / "array.c")
    write(sources_root / "practice-cpp" / "README.md", "# Practice Cpp\n")
    write(sources_root / "practice-cpp" / "arrays" / "jvector.h")
    write(sources_root / "practice-python" / "README.md", "# Practice Python\n")
    write(sources_root / "practice-python" / "arrays" / "array.py")

    first = build_manifest(sources_root)
    second = build_manifest(sources_root)

    assert yaml.safe_dump(first, sort_keys=False) == yaml.safe_dump(second, sort_keys=False)
    assert [source["id"] for source in first["sources"]] == sorted(
        source["id"] for source in first["sources"]
    )

    asset_by_id = {asset["id"]: asset for asset in first["assets"]}
    assert asset_by_id["icc-anki-coding"]["storage_path"] == "ICC/flashcards/Coding.apkg"
    assert asset_by_id["ciu-flashcards-standard"]["format"] == "db"
    assert asset_by_id["sdp-flashcards-system-design"]["course_code"] == "SDP"

    module_ids = [module["id"] for track in first["tracks"] for module in track["modules"]]
    lesson_count = sum(
        len(module["lessons"]) for track in first["tracks"] for module in track["modules"]
    )

    assert "ie-module-foundations-and-study-setup" in module_ids
    assert len(module_ids) >= 13
    assert lesson_count >= 107
    assert "ie-module-practice-c" in module_ids
    assert "ie-module-practice-cpp" in module_ids
    assert "ie-module-practice-python" in module_ids
    assert first["meta"]["estimated_total_hours"] > 238
