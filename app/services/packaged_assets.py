"""Expose packaged curriculum assets through the course-file browser.

Production images include a whitelisted subset of binary curriculum assets
under `/app/curriculum/sources`, but the dashboard Files pane and MCP file
tools intentionally browse only `STUDY_ROOT`. This module copies the manifest
asset files into a managed course folder inside `STUDY_ROOT` at startup.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_MANIFEST = "/app/curriculum/interview_manifest.v2.yaml"
DEFAULT_SOURCES_ROOT = "/app/curriculum/sources"
DEFAULT_DESTINATION_PREFIX = "interview-engineering/resources/flashcards"
SUPPORTED_FORMATS = {"apkg", "db"}


def _study_root() -> Path:
    return Path(os.environ.get("STUDY_ROOT", "/opt/courses"))


def _packaged_manifest_path() -> Path:
    return Path(os.environ.get("PACKAGED_CURRICULUM_MANIFEST", DEFAULT_MANIFEST))


def _packaged_sources_root() -> Path:
    return Path(os.environ.get("PACKAGED_CURRICULUM_ASSETS_ROOT", DEFAULT_SOURCES_ROOT))


def _destination_prefix() -> str:
    return (
        os.environ.get("PACKAGED_CURRICULUM_DESTINATION_PREFIX", DEFAULT_DESTINATION_PREFIX)
        .strip()
        .strip("/")
    )


def _safe_child(root: Path, relative_path: str) -> Path:
    clean = (relative_path or "").strip().lstrip("/")
    target = (root / clean).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError(f"path escapes root: {relative_path!r}")
    return target


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    with manifest_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a mapping: {manifest_path}")
    return data


def _asset_source_path(
    asset: dict[str, Any],
    *,
    sources_by_id: dict[str, Path],
    sources_root: Path,
) -> Path | None:
    source = asset.get("source")
    if not isinstance(source, dict):
        return None
    repo_id = str(source.get("repo") or "").strip()
    source_path = str(source.get("path") or "").strip()
    if not repo_id or not source_path:
        return None
    repo_root = sources_by_id.get(repo_id, sources_root / repo_id)
    return _safe_child(repo_root, source_path)


def _copy_if_needed(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.is_file():
        source_stat = source.stat()
        destination_stat = destination.stat()
        if (
            destination_stat.st_size == source_stat.st_size
            and destination.read_bytes() == source.read_bytes()
        ):
            return "skipped"

    tmp = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, destination)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return "copied"


def sync_packaged_curriculum_assets(
    *,
    manifest_path: Path | None = None,
    sources_root: Path | None = None,
    study_root: Path | None = None,
    destination_prefix: str | None = None,
) -> dict[str, Any]:
    """Copy packaged manifest assets into `STUDY_ROOT`.

    The operation is deterministic and idempotent: destination paths are
    derived from the manifest asset list sorted by ID, and files are replaced
    only when their bytes differ from the packaged source.
    """
    manifest_path = manifest_path or _packaged_manifest_path()
    sources_root = sources_root or _packaged_sources_root()
    study_root = study_root or _study_root()
    destination_prefix = (
        destination_prefix.strip().strip("/")
        if destination_prefix is not None
        else _destination_prefix()
    )

    summary: dict[str, Any] = {
        "enabled": True,
        "manifest": str(manifest_path),
        "sources_root": str(sources_root),
        "study_root": str(study_root),
        "destination_prefix": destination_prefix,
        "copied": 0,
        "skipped": 0,
        "missing": [],
        "files": [],
    }

    if not manifest_path.is_file():
        summary["enabled"] = False
        summary["reason"] = "manifest not found"
        return summary
    if not sources_root.is_dir():
        summary["enabled"] = False
        summary["reason"] = "packaged sources root not found"
        return summary

    manifest = _load_manifest(manifest_path)
    sources_by_id: dict[str, Path] = {}
    for source in manifest.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "").strip()
        source_path = str(source.get("path") or "").strip()
        if not source_id:
            continue
        if source_path.startswith("curriculum/sources/"):
            source_path = source_path.removeprefix("curriculum/sources/")
        sources_by_id[source_id] = _safe_child(sources_root, source_path)

    assets = [
        asset
        for asset in (manifest.get("assets") or [])
        if isinstance(asset, dict)
        and str(asset.get("format") or "").lower() in SUPPORTED_FORMATS
    ]
    for asset in sorted(assets, key=lambda item: str(item.get("id") or "")):
        source = _asset_source_path(
            asset,
            sources_by_id=sources_by_id,
            sources_root=sources_root,
        )
        if source is None or not source.is_file():
            summary["missing"].append(str(asset.get("id") or source or "unknown"))
            continue

        destination = _safe_child(study_root, f"{destination_prefix}/{source.name}")
        action = _copy_if_needed(source, destination)
        summary[action] += 1
        summary["files"].append(str(destination.relative_to(study_root.resolve())))

    return summary


async def sync_packaged_curriculum_assets_on_startup() -> dict[str, Any] | None:
    """Run the packaged asset sync when enabled for the runtime image."""
    enabled = os.environ.get("OPENSTUDY_PACKAGED_CURRICULUM", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None

    try:
        summary = await asyncio.to_thread(sync_packaged_curriculum_assets)
    except Exception as exc:
        log.warning("packaged curriculum asset sync failed: %s", exc)
        return {"enabled": False, "error": str(exc)}

    if summary.get("enabled"):
        log.info(
            "packaged curriculum assets synced: copied=%s skipped=%s missing=%s destination=%s",
            summary["copied"],
            summary["skipped"],
            len(summary["missing"]),
            summary["destination_prefix"],
        )
    else:
        log.info("packaged curriculum asset sync skipped: %s", summary.get("reason"))
    return summary
