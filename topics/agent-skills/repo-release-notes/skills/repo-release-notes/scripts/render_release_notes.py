#!/usr/bin/env python3
"""Validate an editorial plan against collected Git evidence and render Markdown."""

from __future__ import annotations

import argparse
import json
import re
import string
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
SECTION_ORDER = ("Added", "Changed", "Fixed", "Security", "Deprecated", "Breaking")
AUDIENCES = {
    "end-users": "End users",
    "developers": "Developers",
    "operators": "Operators",
    "internal": "Internal",
}
FILE_STATUSES = {
    "added",
    "broken-pair",
    "copied",
    "deleted",
    "modified",
    "renamed",
    "type-changed",
    "unmerged",
    "unknown",
}
PLAN_FIELDS = {
    "schema_version",
    "release_name",
    "release_date",
    "audience",
    "summary",
    "compare_url",
    "sections",
    "upgrade_notes",
    "known_issues",
}
REQUIRED_PLAN_FIELDS = {"schema_version", "release_name", "audience", "summary", "sections"}
TEMPLATE_FIELDS = {
    "release_name",
    "release_date",
    "audience",
    "summary",
    "sections",
    "upgrade_notes",
    "known_issues",
    "compare_link",
}
MAX_INPUT_BYTES = 10_000_000
MAX_SUMMARY_LENGTH = 1_000
MAX_ITEM_LENGTH = 500
OID_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
REFERENCE_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
RAW_HTML_PATTERN = re.compile(r"</?[A-Za-z][^>]*>")


class RenderError(RuntimeError):
    """A user-facing plan, evidence, template, or output error."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RenderError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, label: str) -> Any:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise RenderError(f"{label} file is larger than {MAX_INPUT_BYTES} bytes")
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except FileNotFoundError as exc:
        raise RenderError(f"{label} file does not exist: {path}") from exc
    except UnicodeDecodeError as exc:
        raise RenderError(f"{label} file must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise RenderError(
            f"{label} file is not valid JSON: line {exc.lineno}, column {exc.colno}"
        ) from exc


def _object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderError(f"{label} must be a JSON object")
    return value


def _exact_fields(
    value: Mapping[str, Any],
    *,
    label: str,
    allowed: set[str],
    required: set[str],
) -> None:
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise RenderError(f"{label} has unknown field(s): {', '.join(unknown)}")
    if missing:
        raise RenderError(f"{label} is missing field(s): {', '.join(missing)}")


def _integer(value: Any, *, label: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RenderError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise RenderError(f"{label} must be at least {minimum}")
    return value


def _single_line_text(
    value: Any,
    *,
    label: str,
    maximum: int,
    allow_html_like_text: bool = False,
) -> str:
    if not isinstance(value, str):
        raise RenderError(f"{label} must be a string")
    text = value.strip()
    if not text:
        raise RenderError(f"{label} must not be empty")
    if len(text) > maximum:
        raise RenderError(f"{label} must be at most {maximum} characters")
    if any(
        character in "\r\n" or unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in text
    ):
        raise RenderError(f"{label} must be one printable line")
    if not allow_html_like_text and RAW_HTML_PATTERN.search(text):
        raise RenderError(f"{label} must not contain raw HTML")
    return text


def _oid(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not OID_PATTERN.fullmatch(value.lower()):
        raise RenderError(f"{label} must be a 40- or 64-character hexadecimal object ID")
    return value.lower()


def _path_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise RenderError(f"{label} must be a string")
    if not value:
        raise RenderError(f"{label} must not be empty")
    if len(value) > 4_096:
        raise RenderError(f"{label} must be at most 4096 characters")
    if "\0" in value:
        raise RenderError(f"{label} must not contain a NUL character")
    return value


def _validate_file_change(value: Any, *, label: str) -> None:
    change = _object(value, label=label)
    status = change.get("status")
    if status not in FILE_STATUSES:
        raise RenderError(f"{label}.status is not supported")
    rename_or_copy = status in {"renamed", "copied"}
    allowed = {"status", "path", "additions", "deletions", "binary"}
    required = {"status", "path", "additions", "deletions", "binary"}
    if rename_or_copy:
        allowed |= {"previous_path", "similarity"}
        required.add("previous_path")
    _exact_fields(change, label=label, allowed=allowed, required=required)
    _path_text(change["path"], label=f"{label}.path")
    if rename_or_copy:
        _path_text(change["previous_path"], label=f"{label}.previous_path")
        if "similarity" in change:
            similarity = _integer(change["similarity"], label=f"{label}.similarity", minimum=0)
            if similarity > 100:
                raise RenderError(f"{label}.similarity must not exceed 100")
    if not isinstance(change["binary"], bool):
        raise RenderError(f"{label}.binary must be a boolean")
    if change["binary"]:
        if change["additions"] is not None or change["deletions"] is not None:
            raise RenderError(f"{label} binary statistics must use null additions and deletions")
    else:
        _integer(change["additions"], label=f"{label}.additions", minimum=0)
        _integer(change["deletions"], label=f"{label}.deletions", minimum=0)


def validate_changes(value: Any) -> dict[str, Any]:
    changes = _object(value, label="changes")
    _exact_fields(
        changes,
        label="changes",
        allowed={"schema_version", "range", "commits"},
        required={"schema_version", "range", "commits"},
    )
    if _integer(changes["schema_version"], label="changes.schema_version") != SCHEMA_VERSION:
        raise RenderError(f"changes.schema_version must be {SCHEMA_VERSION}")

    range_data = _object(changes["range"], label="changes.range")
    _exact_fields(
        range_data,
        label="changes.range",
        allowed={"base", "head", "base_oid", "head_oid", "commit_count"},
        required={"base", "head", "base_oid", "head_oid", "commit_count"},
    )
    _single_line_text(
        range_data["base"],
        label="changes.range.base",
        maximum=1_024,
        allow_html_like_text=True,
    )
    _single_line_text(
        range_data["head"],
        label="changes.range.head",
        maximum=1_024,
        allow_html_like_text=True,
    )
    _oid(range_data["base_oid"], label="changes.range.base_oid")
    head_oid = _oid(range_data["head_oid"], label="changes.range.head_oid")

    commits = changes["commits"]
    if not isinstance(commits, list) or not commits:
        raise RenderError("changes.commits must be a non-empty array")
    expected_count = _integer(
        range_data["commit_count"], label="changes.range.commit_count", minimum=1
    )
    if expected_count != len(commits):
        raise RenderError("changes.range.commit_count does not match changes.commits")

    seen: set[str] = set()
    for index, raw_commit in enumerate(commits):
        label = f"changes.commits[{index}]"
        commit = _object(raw_commit, label=label)
        _exact_fields(
            commit,
            label=label,
            allowed={"sha", "short_sha", "subject", "files"},
            required={"sha", "short_sha", "subject", "files"},
        )
        sha = _oid(commit["sha"], label=f"{label}.sha")
        if sha in seen:
            raise RenderError(f"{label}.sha is duplicated")
        seen.add(sha)
        if commit["short_sha"] != sha[:12]:
            raise RenderError(f"{label}.short_sha must be the first 12 characters of sha")
        _single_line_text(
            commit["subject"],
            label=f"{label}.subject",
            maximum=10_000,
            allow_html_like_text=True,
        )
        if not isinstance(commit["files"], list):
            raise RenderError(f"{label}.files must be an array")
        for file_index, file_change in enumerate(commit["files"]):
            _validate_file_change(file_change, label=f"{label}.files[{file_index}]")

    if head_oid not in seen:
        raise RenderError("changes.range.head_oid must appear in changes.commits")
    return changes


def _resolve_evidence(reference: str, commits: Sequence[Mapping[str, Any]], *, label: str) -> str:
    if not REFERENCE_PATTERN.fullmatch(reference):
        raise RenderError(f"{label} must be a 7- to 64-character hexadecimal commit prefix")
    normalized = reference.lower()
    matches = [
        str(commit["sha"]) for commit in commits if str(commit["sha"]).startswith(normalized)
    ]
    if not matches:
        raise RenderError(f"{label} does not match a collected commit: {reference}")
    if len(matches) > 1:
        raise RenderError(f"{label} is ambiguous in the collected range: {reference}")
    return matches[0]


def _validate_item(
    value: Any,
    *,
    label: str,
    commits: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    item = _object(value, label=label)
    _exact_fields(
        item,
        label=label,
        allowed={"text", "commits"},
        required={"text", "commits"},
    )
    text = _single_line_text(item["text"], label=f"{label}.text", maximum=MAX_ITEM_LENGTH)
    references = item["commits"]
    if not isinstance(references, list) or not references:
        raise RenderError(f"{label}.commits must be a non-empty array")
    if not all(isinstance(reference, str) for reference in references):
        raise RenderError(f"{label}.commits entries must be strings")

    resolved: list[str] = []
    for index, reference in enumerate(references):
        sha = _resolve_evidence(reference, commits, label=f"{label}.commits[{index}]")
        if sha in resolved:
            raise RenderError(f"{label}.commits contains duplicate evidence")
        resolved.append(sha)
    return {"text": text, "commits": resolved}


def _validate_item_array(
    value: Any,
    *,
    label: str,
    commits: Sequence[Mapping[str, Any]],
    allow_empty: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RenderError(f"{label} must be an array")
    if not value and not allow_empty:
        raise RenderError(f"{label} must not be empty when present")
    return [
        _validate_item(item, label=f"{label}[{index}]", commits=commits)
        for index, item in enumerate(value)
    ]


def _reject_duplicate_item_texts(plan: Mapping[str, Any]) -> None:
    seen: dict[str, str] = {}
    groups: list[tuple[str, Sequence[Mapping[str, Any]]]] = [
        (f"plan.sections.{heading}", plan["sections"][heading])
        for heading in SECTION_ORDER
        if heading in plan["sections"]
    ]
    groups.extend(
        (f"plan.{field}", plan[field])
        for field in ("upgrade_notes", "known_issues")
        if field in plan
    )

    for group_label, items in groups:
        for index, item in enumerate(items):
            label = f"{group_label}[{index}]"
            key = str(item["text"]).casefold()
            if key in seen:
                raise RenderError(f"{label} duplicates item text from {seen[key]}")
            seen[key] = label


def _https_url(value: Any, *, label: str) -> str:
    text = _single_line_text(value, label=label, maximum=2_048)
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RenderError(f"{label} must be an absolute HTTPS URL without credentials")
    if any(character in text for character in "<>()"):
        raise RenderError(f"{label} contains a character unsafe for a Markdown link")
    return text


def validate_plan(value: Any, changes: Mapping[str, Any]) -> dict[str, Any]:
    plan = _object(value, label="plan")
    _exact_fields(
        plan,
        label="plan",
        allowed=PLAN_FIELDS,
        required=REQUIRED_PLAN_FIELDS,
    )
    if _integer(plan["schema_version"], label="plan.schema_version") != SCHEMA_VERSION:
        raise RenderError(f"plan.schema_version must be {SCHEMA_VERSION}")

    release_name = _single_line_text(plan["release_name"], label="plan.release_name", maximum=100)
    audience = plan["audience"]
    if audience not in AUDIENCES:
        choices = ", ".join(AUDIENCES)
        raise RenderError(f"plan.audience must be one of: {choices}")
    summary = _single_line_text(plan["summary"], label="plan.summary", maximum=MAX_SUMMARY_LENGTH)

    release_date: str | None = None
    if "release_date" in plan:
        release_date = _single_line_text(
            plan["release_date"], label="plan.release_date", maximum=10
        )
        try:
            parsed_date = date.fromisoformat(release_date)
        except ValueError as exc:
            raise RenderError("plan.release_date must use YYYY-MM-DD") from exc
        if parsed_date.isoformat() != release_date:
            raise RenderError("plan.release_date must use YYYY-MM-DD")

    compare_url = None
    if "compare_url" in plan:
        compare_url = _https_url(plan["compare_url"], label="plan.compare_url")

    raw_sections = _object(plan["sections"], label="plan.sections")
    unknown_sections = sorted(set(raw_sections) - set(SECTION_ORDER))
    if unknown_sections:
        raise RenderError(f"plan.sections has unknown heading(s): {', '.join(unknown_sections)}")
    commits = changes["commits"]
    sections: dict[str, list[dict[str, Any]]] = {}
    for heading in SECTION_ORDER:
        if heading not in raw_sections:
            continue
        items = _validate_item_array(
            raw_sections[heading],
            label=f"plan.sections.{heading}",
            commits=commits,
            allow_empty=True,
        )
        if items:
            sections[heading] = items
    if not sections:
        raise RenderError("plan.sections must contain at least one non-empty approved section")

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "release_name": release_name,
        "audience": audience,
        "summary": summary,
        "sections": sections,
    }
    if release_date is not None:
        result["release_date"] = release_date
    if compare_url is not None:
        result["compare_url"] = compare_url
    for field in ("upgrade_notes", "known_issues"):
        if field in plan:
            result[field] = _validate_item_array(
                plan[field],
                label=f"plan.{field}",
                commits=commits,
                allow_empty=False,
            )
    _reject_duplicate_item_texts(result)
    return result


def _render_item(item: Mapping[str, Any]) -> str:
    references = [f"`{sha[:7]}`" for sha in item["commits"]]
    label = "commit" if len(references) == 1 else "commits"
    return f"- {item['text']} ({label}: {', '.join(references)})"


def _render_group(heading: str, items: Sequence[Mapping[str, Any]]) -> str:
    body = "\n".join(_render_item(item) for item in items)
    return f"## {heading}\n\n{body}"


def _load_template(path: Path) -> string.Template:
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RenderError(f"template file does not exist: {path}") from exc
    except UnicodeDecodeError as exc:
        raise RenderError("template file must be UTF-8") from exc
    template = string.Template(source)
    if not template.is_valid():
        raise RenderError("template contains an invalid placeholder")
    fields = set(template.get_identifiers())
    unknown = sorted(fields - TEMPLATE_FIELDS)
    if unknown:
        raise RenderError(f"template has unknown placeholder(s): {', '.join(unknown)}")
    missing = sorted({"release_name", "summary", "sections"} - fields)
    if missing:
        raise RenderError(f"template is missing placeholder(s): {', '.join(missing)}")
    return template


def render_release_notes(plan: Mapping[str, Any], template: string.Template) -> str:
    sections = "\n\n".join(
        _render_group(heading, plan["sections"][heading])
        for heading in SECTION_ORDER
        if heading in plan["sections"]
    )
    upgrade_notes = (
        _render_group("Upgrade notes", plan["upgrade_notes"]) if "upgrade_notes" in plan else ""
    )
    known_issues = (
        _render_group("Known issues", plan["known_issues"]) if "known_issues" in plan else ""
    )
    compare_link = (
        f"[Compare the complete range]({plan['compare_url']})" if "compare_url" in plan else ""
    )
    rendered = template.substitute(
        release_name=plan["release_name"],
        release_date=(f"_Released {plan['release_date']}_" if "release_date" in plan else ""),
        audience=AUDIENCES[plan["audience"]],
        summary=plan["summary"],
        sections=sections,
        upgrade_notes=upgrade_notes,
        known_issues=known_issues,
        compare_link=compare_link,
    )
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
    return rendered.rstrip() + "\n"


def _default_template_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "release-notes-template.md"


def _write_markdown(markdown: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(markdown)
        return
    output.write_text(markdown, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render release notes after checking every item against collected Git evidence."
    )
    parser.add_argument("--changes", type=Path, required=True, help="JSON from collect_changes.py")
    parser.add_argument("--plan", type=Path, required=True, help="Editorial release-plan JSON")
    parser.add_argument("--template", type=Path, help="Optional Markdown template override")
    parser.add_argument(
        "--output", type=Path, help="Write Markdown here instead of standard output"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        changes = validate_changes(_read_json(args.changes, label="changes"))
        plan = validate_plan(_read_json(args.plan, label="plan"), changes)
        template = _load_template(args.template or _default_template_path())
        _write_markdown(render_release_notes(plan, template), args.output)
    except (OSError, RenderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
