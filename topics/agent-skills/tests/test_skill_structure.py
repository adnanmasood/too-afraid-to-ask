from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from conftest import COLLECT_SCRIPT, PLUGIN_ROOT, RENDER_SCRIPT, SKILL_ROOT


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw, _ = text.split("---", maxsplit=2)
    result = {}
    for line in raw.strip().splitlines():
        key, value = line.split(":", maxsplit=1)
        result[key.strip()] = value.strip()
    return result


def test_portable_skill_has_expected_progressive_disclosure_layout() -> None:
    expected = {
        SKILL_ROOT / "SKILL.md",
        SKILL_ROOT / "agents" / "openai.yaml",
        SKILL_ROOT / "assets" / "release-notes-template.md",
        SKILL_ROOT / "references" / "release-notes-policy.md",
        COLLECT_SCRIPT,
        RENDER_SCRIPT,
    }

    assert all(path.is_file() for path in expected)


def test_skill_frontmatter_is_discriminating_and_matches_folder() -> None:
    metadata = _frontmatter(SKILL_ROOT / "SKILL.md")

    assert metadata["name"] == SKILL_ROOT.name == "repo-release-notes"
    assert "Git history" in metadata["description"]
    assert "do not use" in metadata["description"]
    assert set(metadata) == {"name", "description"}


def test_skill_links_resolve_inside_the_skill() -> None:
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]+\]\(([^)]+)\)", text)

    assert links == ["references/release-notes-policy.md"]
    assert all((SKILL_ROOT / link).is_file() for link in links)
    assert "assets/release-notes-template.md" in text


def test_openai_metadata_is_consistent_and_implicitly_discoverable() -> None:
    text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert 'display_name: "Repository Release Notes"' in text
    assert 'default_prompt: "Use $repo-release-notes ' in text
    assert "allow_implicit_invocation: true" in text
    assert 25 <= len("Draft grounded release notes from Git history") <= 64


def test_codex_and_claude_manifests_share_portable_identity() -> None:
    codex = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())
    claude = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())

    for field in (
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
    ):
        assert codex[field] == claude[field]
    assert codex["name"] == PLUGIN_ROOT.name
    assert codex["skills"] == "./skills/"
    assert "skills" not in claude
    assert codex["license"] == "Apache-2.0"


def test_companion_scripts_use_only_the_standard_library() -> None:
    for path in (COLLECT_SCRIPT, RENDER_SCRIPT):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.partition(".")[0])

        assert imported <= sys.stdlib_module_names | {"__future__"}


def test_no_scaffold_placeholders_remain() -> None:
    text_files = [
        path
        for path in PLUGIN_ROOT.rglob("*")
        if path.is_file() and path.suffix in {".json", ".md", ".py", ".yaml"}
    ]

    assert text_files
    for path in text_files:
        text = path.read_text(encoding="utf-8")
        assert "[TODO:" not in text
        assert "REPLACE_ME" not in text
