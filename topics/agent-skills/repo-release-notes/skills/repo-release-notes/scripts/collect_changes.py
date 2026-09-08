#!/usr/bin/env python3
"""Collect a deterministic, read-only snapshot of a Git commit range as JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_ERROR_DETAIL = 300


class CollectionError(RuntimeError):
    """A user-facing failure while reading repository history."""


def _safe_detail(value: bytes | str) -> str:
    """Return a bounded, printable, single-line diagnostic."""

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    cleaned = " ".join(_clean_text(value, multiline=False).split())
    return cleaned[:MAX_ERROR_DETAIL] or "Git command failed"


def _clean_text(value: str, *, multiline: bool) -> str:
    """Remove invisible controls while retaining useful commit-message text."""

    value = value.replace("\r\n", "\n").replace("\r", "\n")
    characters: list[str] = []
    for character in value:
        if character == "\n" and multiline:
            characters.append(character)
            continue
        if character == "\t":
            characters.append(" " if not multiline else character)
            continue
        if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            continue
        characters.append(character)

    cleaned = "".join(characters)
    if not multiline:
        return " ".join(cleaned.split())

    lines = [line.rstrip() for line in cleaned.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _run_git(repo: Path, arguments: Sequence[str]) -> bytes:
    """Run Git without a shell and return stdout or raise a safe error."""

    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "--no-pager", *arguments],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise CollectionError("Git is not installed or is not available on PATH") from exc

    if result.returncode != 0:
        raise CollectionError(_safe_detail(result.stderr))
    return result.stdout


def _repository_root(repo: Path) -> Path:
    if not repo.exists():
        raise CollectionError(f"repository path does not exist: {repo}")
    if not repo.is_dir():
        raise CollectionError(f"repository path is not a directory: {repo}")
    output = _run_git(repo, ["rev-parse", "--show-toplevel"])
    return Path(output.decode("utf-8", errors="replace").strip()).resolve()


def _resolve_commit(repo: Path, ref: str, *, label: str) -> str:
    if not ref.strip():
        raise CollectionError(f"{label} ref must not be empty")
    try:
        output = _run_git(
            repo,
            ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        )
    except CollectionError as exc:
        raise CollectionError(f"cannot resolve {label} ref {ref!r}: {exc}") from exc
    oid = output.decode("ascii", errors="replace").strip().lower()
    if not oid or any(character not in "0123456789abcdef" for character in oid):
        raise CollectionError(f"Git returned an invalid object ID for {label} ref {ref!r}")
    return oid


def _latest_reachable_tag(repo: Path, head_oid: str) -> str:
    try:
        output = _run_git(repo, ["describe", "--tags", "--abbrev=0", head_oid])
    except CollectionError as exc:
        raise CollectionError(
            "no tag is reachable from head; pass --base with an explicit commit, tag, or branch"
        ) from exc
    tag = _clean_text(output.decode("utf-8", errors="replace"), multiline=False)
    if not tag:
        raise CollectionError(
            "no tag is reachable from head; pass --base with an explicit commit, tag, or branch"
        )
    return tag


def _require_ancestor(repo: Path, base_oid: str, head_oid: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "--no-pager", "merge-base", "--is-ancestor", base_oid, head_oid],
        check=False,
        capture_output=True,
    )
    if result.returncode == 1:
        raise CollectionError("base ref must be an ancestor of head ref")
    if result.returncode != 0:
        raise CollectionError(_safe_detail(result.stderr))


def _decode_path(value: bytes) -> str:
    # Git's `-z` output already provides an unambiguous NUL-delimited path. Preserve every other
    # character—including repeated spaces, tabs, and newlines—so distinct repository paths never
    # collapse into the same evidence key. Invalid UTF-8 is replaced deterministically for JSON.
    return value.decode("utf-8", errors="replace")


def _parse_name_status(payload: bytes) -> list[dict[str, Any]]:
    """Parse `git diff --name-status -z` output without shell quoting rules."""

    fields = payload.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()

    status_names = {
        "A": "added",
        "B": "broken-pair",
        "C": "copied",
        "D": "deleted",
        "M": "modified",
        "R": "renamed",
        "T": "type-changed",
        "U": "unmerged",
        "X": "unknown",
    }
    changes: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        raw_status = fields[index].decode("ascii", errors="replace")
        index += 1
        kind = raw_status[:1]
        if kind not in status_names:
            raise CollectionError(f"unsupported Git file status: {raw_status!r}")

        if kind in {"C", "R"}:
            if index + 1 >= len(fields):
                raise CollectionError("Git returned an incomplete rename or copy record")
            previous_path = _decode_path(fields[index])
            path = _decode_path(fields[index + 1])
            index += 2
            change: dict[str, Any] = {
                "status": status_names[kind],
                "path": path,
                "previous_path": previous_path,
            }
            score = raw_status[1:]
            if score.isdigit():
                change["similarity"] = int(score)
            changes.append(change)
            continue

        if index >= len(fields):
            raise CollectionError("Git returned an incomplete file-status record")
        changes.append(
            {
                "status": status_names[kind],
                "path": _decode_path(fields[index]),
            }
        )
        index += 1

    return sorted(changes, key=lambda item: (item["path"], item.get("previous_path", "")))


def _parse_numstat(payload: bytes) -> dict[tuple[str, str | None], dict[str, Any]]:
    """Parse `git diff --numstat -z`, including its three-field rename form."""

    fields = payload.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()

    statistics: dict[tuple[str, str | None], dict[str, Any]] = {}
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        parts = record.split(b"\t", maxsplit=2)
        if len(parts) != 3:
            raise CollectionError("Git returned an incomplete numstat record")
        raw_additions, raw_deletions, raw_path = parts

        previous_path: str | None = None
        if raw_path:
            path = _decode_path(raw_path)
        else:
            if index + 1 >= len(fields):
                raise CollectionError("Git returned an incomplete renamed numstat record")
            previous_path = _decode_path(fields[index])
            path = _decode_path(fields[index + 1])
            index += 2

        binary = raw_additions == raw_deletions == b"-"
        if binary:
            additions = deletions = None
        else:
            try:
                additions = int(raw_additions)
                deletions = int(raw_deletions)
            except ValueError as exc:
                raise CollectionError("Git returned non-numeric line statistics") from exc

        key = (path, previous_path)
        if key in statistics:
            raise CollectionError(f"Git returned duplicate line statistics for {path!r}")
        statistics[key] = {
            "additions": additions,
            "deletions": deletions,
            "binary": binary,
        }
    return statistics


def _diff_output(repo: Path, oid: str, parents: list[str], mode: str) -> bytes:
    common = [mode, "-z", "--find-renames=50%"]
    if parents:
        return _run_git(repo, ["diff", *common, parents[0], oid])
    return _run_git(
        repo,
        ["diff-tree", "--root", "--no-commit-id", "-r", *common, oid],
    )


def _commit_files(repo: Path, oid: str, parents: list[str]) -> list[dict[str, Any]]:
    changes = _parse_name_status(_diff_output(repo, oid, parents, "--name-status"))
    statistics = _parse_numstat(_diff_output(repo, oid, parents, "--numstat"))
    for change in changes:
        key = (change["path"], change.get("previous_path"))
        try:
            change.update(statistics.pop(key))
        except KeyError as exc:
            raise CollectionError(
                f"Git did not return line statistics for {change['path']!r}"
            ) from exc
    if statistics:
        unexpected_path = next(iter(statistics))[0]
        raise CollectionError(f"Git returned unmatched line statistics for {unexpected_path!r}")
    return changes


def _commit_record(repo: Path, oid: str) -> dict[str, Any]:
    parent_line = _run_git(repo, ["rev-list", "--parents", "-n", "1", oid])
    parent_fields = parent_line.decode("ascii", errors="replace").strip().split()
    parents = parent_fields[1:]

    metadata = _run_git(
        repo,
        [
            "show",
            "-s",
            "--no-show-signature",
            "--format=%H%x00%s%x00",
            oid,
        ],
    )
    fields = metadata.split(b"\0", maxsplit=2)
    if len(fields) < 3:
        raise CollectionError(f"Git returned incomplete metadata for commit {oid[:12]}")

    sha = fields[0].decode("ascii", errors="replace").lower()
    subject = _clean_text(fields[1].decode("utf-8", errors="replace"), multiline=False)
    return {
        "sha": sha,
        "short_sha": sha[:12],
        "subject": subject or "(no subject)",
        "files": _commit_files(repo, oid, parents),
    }


def collect_changes(repo: Path, base: str | None, head: str) -> dict[str, Any]:
    """Return a JSON-serializable snapshot for the base-exclusive release range."""

    root = _repository_root(repo)
    head_oid = _resolve_commit(root, head, label="head")
    selected_base = base if base is not None else _latest_reachable_tag(root, head_oid)
    base_oid = _resolve_commit(root, selected_base, label="base")
    _require_ancestor(root, base_oid, head_oid)

    output = _run_git(root, ["rev-list", "--reverse", "--topo-order", f"{base_oid}..{head_oid}"])
    commit_ids = [line for line in output.decode("ascii", errors="replace").splitlines() if line]
    if not commit_ids:
        raise CollectionError("the selected range contains no commits")

    commits = [_commit_record(root, oid) for oid in commit_ids]
    return {
        "schema_version": SCHEMA_VERSION,
        "range": {
            "base": selected_base,
            "head": head,
            "base_oid": base_oid,
            "head_oid": head_oid,
            "commit_count": len(commits),
        },
        "commits": commits,
    }


def _write_json(payload: dict[str, Any], output: Path | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        sys.stdout.write(serialized)
        return
    output.write_text(serialized, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect an evidence snapshot for release notes from a local Git range."
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Git repository (default: .)")
    parser.add_argument(
        "--base",
        help="Ancestor ref to exclude (default: latest tag reachable from --head)",
    )
    parser.add_argument(
        "--head", default="HEAD", help="Head commit, tag, or branch (default: HEAD)"
    )
    parser.add_argument("--output", type=Path, help="Write JSON here instead of standard output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = collect_changes(args.repo, args.base, args.head)
        _write_json(payload, args.output)
    except (CollectionError, OSError) as exc:
        print(f"error: {_safe_detail(str(exc))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
