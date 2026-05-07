"""Compatibility wrapper for the canonical curriculum manifest generator.

Fresh schema v2 manifest creation now lives in create_manifest.py. This wrapper
keeps the old command from failing while directing it to regenerate the
canonical manifest path.
"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from scripts.curriculum.create_manifest import main as create_manifest_main

    return create_manifest_main(["--force"])


if __name__ == "__main__":
    raise SystemExit(main())
