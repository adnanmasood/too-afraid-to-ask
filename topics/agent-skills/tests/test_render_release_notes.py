from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES, GOLDEN, RENDER_SCRIPT, load_script


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RENDER_SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _fixture_command(*extra: str) -> tuple[str, ...]:
    return (
        "--changes",
        str(FIXTURES / "changes.json"),
        "--plan",
        str(FIXTURES / "release-plan.json"),
        *extra,
    )


def test_fixture_renders_exact_golden_markdown() -> None:
    result = _run(*_fixture_command())

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == (GOLDEN / "release-notes.md").read_text(encoding="utf-8")


def test_output_file_receives_markdown_and_stdout_stays_clean(tmp_path: Path) -> None:
    output = tmp_path / "release-notes.md"

    result = _run(*_fixture_command("--output", str(output)))

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert output.read_text(encoding="utf-8") == (GOLDEN / "release-notes.md").read_text()


def test_section_order_is_canonical_and_empty_sections_are_omitted(
    tmp_path: Path, release_plan: dict
) -> None:
    release_plan["sections"] = {
        "Breaking": [{"text": "Use the new configuration key.", "commits": ["2222222"]}],
        "Deprecated": [],
        "Added": [{"text": "Added setup guidance.", "commits": ["1111111"]}],
    }
    plan_path = _write_json(tmp_path / "plan.json", release_plan)

    result = _run("--changes", str(FIXTURES / "changes.json"), "--plan", str(plan_path))

    assert result.returncode == 0
    assert "## Deprecated" not in result.stdout
    assert result.stdout.index("## Added") < result.stdout.index("## Breaking")


def test_optional_date_upgrade_notes_and_known_issues(tmp_path: Path, release_plan: dict) -> None:
    del release_plan["release_date"]
    del release_plan["compare_url"]
    release_plan["upgrade_notes"] = [
        {"text": "Move project settings into `project.toml`.", "commits": ["2222222"]}
    ]
    release_plan["known_issues"] = [
        {"text": "Checkout discovery still requires Git.", "commits": ["3333333"]}
    ]
    plan_path = _write_json(tmp_path / "plan.json", release_plan)

    result = _run("--changes", str(FIXTURES / "changes.json"), "--plan", str(plan_path))

    assert result.returncode == 0
    assert "_Released" not in result.stdout
    assert "## Upgrade notes" in result.stdout
    assert "## Known issues" in result.stdout
    assert "Compare the complete range" not in result.stdout
    assert "\n\n\n" not in result.stdout


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda plan: plan.update(extra=True), "unknown field"),
        (lambda plan: plan.pop("release_name"), "missing field"),
        (lambda plan: plan.update(schema_version=True), "must be an integer"),
        (lambda plan: plan.update(release_name="  "), "must not be empty"),
        (lambda plan: plan.update(release_date="2026-02-30"), "must use YYYY-MM-DD"),
        (lambda plan: plan.update(audience="customers"), "must be one of"),
        (lambda plan: plan.update(compare_url="http://example.com"), "absolute HTTPS URL"),
        (lambda plan: plan.update(sections={"Removed": []}), "unknown heading"),
        (lambda plan: plan.update(sections={"Added": []}), "at least one non-empty"),
        (lambda plan: plan.update(upgrade_notes=[]), "must not be empty"),
        (lambda plan: plan.update(known_issues=[]), "must not be empty"),
    ],
)
def test_invalid_plan_contract_is_rejected(
    tmp_path: Path, release_plan: dict, mutate, message: str
) -> None:
    mutate(release_plan)
    plan_path = _write_json(tmp_path / "plan.json", release_plan)

    result = _run("--changes", str(FIXTURES / "changes.json"), "--plan", str(plan_path))

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert message in result.stderr


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"text": "A valid note", "commits": ["9999999"]}, "does not match"),
        ({"text": "A valid note", "commits": []}, "non-empty array"),
        ({"text": "A valid note", "commits": ["1111111", "11111111"]}, "duplicate evidence"),
        ({"text": "Two\nlines", "commits": ["1111111"]}, "one printable line"),
        ({"text": "Use <script>alert(1)</script>", "commits": ["1111111"]}, "raw HTML"),
        ({"text": "A valid note", "commits": [1234567]}, "entries must be strings"),
        ({"text": "A valid note", "commits": ["1111111"], "extra": True}, "unknown field"),
    ],
)
def test_invalid_release_items_are_rejected(
    tmp_path: Path, release_plan: dict, item: dict, message: str
) -> None:
    release_plan["sections"] = {"Added": [item]}
    plan_path = _write_json(tmp_path / "plan.json", release_plan)
    output_path = tmp_path / "release-notes.md"
    output_path.write_text("leave existing output untouched\n", encoding="utf-8")

    result = _run(
        "--changes",
        str(FIXTURES / "changes.json"),
        "--plan",
        str(plan_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert message in result.stderr
    assert output_path.read_text(encoding="utf-8") == "leave existing output untouched\n"


def test_duplicate_release_items_are_rejected_without_touching_output(
    tmp_path: Path, release_plan: dict
) -> None:
    item = {"text": "A duplicated user-visible change.", "commits": ["1111111"]}
    release_plan["sections"] = {"Added": [item], "Fixed": [copy.deepcopy(item)]}
    plan_path = _write_json(tmp_path / "plan.json", release_plan)
    output_path = tmp_path / "release-notes.md"
    output_path.write_text("existing draft\n", encoding="utf-8")

    result = _run(
        "--changes",
        str(FIXTURES / "changes.json"),
        "--plan",
        str(plan_path),
        "--output",
        str(output_path),
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "duplicates item text" in result.stderr
    assert output_path.read_text(encoding="utf-8") == "existing draft\n"


def test_ambiguous_short_commit_reference_is_rejected(
    tmp_path: Path, changes_payload: dict, release_plan: dict
) -> None:
    duplicate_prefix = copy.deepcopy(changes_payload["commits"][0])
    duplicate_prefix["sha"] = "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    duplicate_prefix["short_sha"] = duplicate_prefix["sha"][:12]
    changes_payload["commits"].append(duplicate_prefix)
    changes_payload["range"]["commit_count"] += 1
    changes_path = _write_json(tmp_path / "changes.json", changes_payload)
    release_plan["sections"] = {"Added": [{"text": "A valid note", "commits": ["1111111"]}]}
    plan_path = _write_json(tmp_path / "plan.json", release_plan)

    result = _run("--changes", str(changes_path), "--plan", str(plan_path))

    assert result.returncode == 1
    assert "ambiguous" in result.stderr


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda changes: changes.update(extra=True), "unknown field"),
        (
            lambda changes: changes["range"].update(commit_count=99),
            "commit_count does not match",
        ),
        (lambda changes: changes.update(commits=[]), "non-empty array"),
        (
            lambda changes: changes["commits"][0].update(short_sha="abcdef123456"),
            "first 12 characters",
        ),
    ],
)
def test_malformed_changes_snapshot_is_rejected(
    tmp_path: Path, changes_payload: dict, mutate, message: str
) -> None:
    mutate(changes_payload)
    changes_path = _write_json(tmp_path / "changes.json", changes_payload)

    result = _run(
        "--changes",
        str(changes_path),
        "--plan",
        str(FIXTURES / "release-plan.json"),
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert message in result.stderr


def test_duplicate_json_fields_are_rejected(tmp_path: Path) -> None:
    plan = tmp_path / "duplicate.json"
    plan.write_text('{"schema_version": 1, "schema_version": 1}\n', encoding="utf-8")

    result = _run("--changes", str(FIXTURES / "changes.json"), "--plan", str(plan))

    assert result.returncode == 1
    assert "duplicate JSON field" in result.stderr


def test_custom_template_is_supported_and_validated(tmp_path: Path) -> None:
    template = tmp_path / "compact.md"
    template.write_text("# $release_name\n\n$summary\n\n$sections\n", encoding="utf-8")

    result = _run(*_fixture_command("--template", str(template)))

    assert result.returncode == 0
    assert result.stdout.startswith("# v1.4.0\n\nThis release")
    assert "**Audience:**" not in result.stdout


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("# $release_name\n\n$unknown\n\n$summary\n\n$sections\n", "unknown placeholder"),
        ("# $release_name\n\n$summary\n", "missing placeholder"),
        ("# ${release_name\n\n$summary\n\n$sections\n", "invalid placeholder"),
    ],
)
def test_bad_custom_templates_are_rejected(tmp_path: Path, source: str, message: str) -> None:
    template = tmp_path / "bad.md"
    template.write_text(source, encoding="utf-8")

    result = _run(*_fixture_command("--template", str(template)))

    assert result.returncode == 1
    assert result.stdout == ""
    assert message in result.stderr


def test_validation_functions_return_normalized_evidence(
    changes_payload: dict, release_plan: dict
) -> None:
    module = load_script(RENDER_SCRIPT, "render_release_notes_for_test")
    changes_payload["commits"][0]["files"][0]["path"] = "odd\tpath\nwith  spaces.txt"

    changes = module.validate_changes(changes_payload)
    plan = module.validate_plan(release_plan, changes)

    assert plan["sections"]["Added"][0]["commits"] == ["1111111111111111111111111111111111111111"]
