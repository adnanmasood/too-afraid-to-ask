from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

TOPIC_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = TOPIC_ROOT / "repo-release-notes"
SKILL_ROOT = PLUGIN_ROOT / "skills" / "repo-release-notes"
COLLECT_SCRIPT = SKILL_ROOT / "scripts" / "collect_changes.py"
RENDER_SCRIPT = SKILL_ROOT / "scripts" / "render_release_notes.py"
FIXTURES = TOPIC_ROOT / "tests" / "fixtures"
GOLDEN = TOPIC_ROOT / "tests" / "golden"


def load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def changes_payload() -> dict:
    return json.loads((FIXTURES / "changes.json").read_text(encoding="utf-8"))


@pytest.fixture
def release_plan() -> dict:
    return json.loads((FIXTURES / "release-plan.json").read_text(encoding="utf-8"))
