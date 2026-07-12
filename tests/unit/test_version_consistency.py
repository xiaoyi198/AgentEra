"""Project version must stay synchronized across backend and frontend."""

from __future__ import annotations

import json
from pathlib import Path

from multiagent_framework import __version__


ROOT = Path(__file__).resolve().parents[2]


def test_project_version_is_consistent():
    canonical = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
    frontend = json.loads(
        (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )["version"]

    assert canonical == __version__ == frontend


def test_fastapi_exposes_project_version():
    from main import app

    assert app.version == __version__
