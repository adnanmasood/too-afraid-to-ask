#!/usr/bin/env python3
"""Rebuild the editable SVG overlays for the Agent Skills article visuals."""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from textwrap import wrap

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "agent-skills-tutorial"

CARDS = [
    (
        "01-mental-model",
        "01",
        "A skill is a playbook",
        "The model brings general ability. The skill brings a proven way of working.",
    ),
    (
        "02-progressive-disclosure",
        "02",
        "Progressive disclosure",
        "Load the name first, the instructions on demand, and supporting files only when needed.",
    ),
    (
        "03-skill-anatomy",
        "03",
        "A skill is a small folder",
        "Instructions coordinate the work; scripts, references, and assets do specialized jobs.",
    ),
    (
        "04-plan-validate-render",
        "04",
        "Plan → validate → render",
        "Let the model exercise judgment, then let deterministic code enforce the contract.",
    ),
    (
        "05-portable-core",
        "05",
        "One portable core",
        "Keep standard instructions shared; move host-specific presentation into thin wrappers.",
    ),
    (
        "06-explicit-implicit-activation",
        "06",
        "Two ways to activate",
        "Invoke a skill explicitly, or let a precise description match the task.",
    ),
    (
        "07-find-and-share",
        "07",
        "Find, inspect, then share",
        "A public listing is a discovery signal—not a security review.",
    ),
    (
        "08-trust-boundary",
        "08",
        "Treat skills as software",
        "Review the whole package before it reaches your tools, data, and credentials.",
    ),
    (
        "09-host-comparison",
        "09",
        "Same skill, different hosts",
        "The folder can travel. Installation, invocation, and permissions still vary.",
    ),
    (
        "10-quality-loop",
        "10",
        "Quality is a loop",
        "Test realistic requests, observe mistakes, refine narrowly, and verify again.",
    ),
]

CAPTURES = [
    (
        "11-skills-ref-validation",
        "Portable skill validation",
        "Recorded locally · skills-ref 0.1.1",
        [
            ("prompt", "$ python -m pip show skills-ref | rg '^(Name|Version):'"),
            ("text", "Name: skills-ref"),
            ("text", "Version: 0.1.1"),
            ("blank", ""),
            ("prompt", "$ .venv/bin/agentskills validate \\"),
            ("prompt", "    repo-release-notes/skills/repo-release-notes"),
            ("success", "Valid skill: repo-release-notes/skills/repo-release-notes"),
        ],
    ),
    (
        "12-collect-changes",
        "Collecting bounded Git evidence",
        "Recorded locally · read-only collector",
        [
            (
                "prompt",
                "$ python3 repo-release-notes/skills/repo-release-notes/scripts/collect_changes.py \\",
            ),
            ("prompt", "    --repo ../.. --base ad43c78 --head b247a11 --output changes.json"),
            ("blank", ""),
            ("prompt", "$ jq -r '<range and compact commit summary>' changes.json"),
            ("success", "range ad43c78..b247a11 • 5 commits"),
            ("text", "fda6360a7d8b • Modify project title in README • 1 file(s)"),
            ("text", "ca3406fee88a • Fix HTML entities in project title in README • 1 file(s)"),
            (
                "accent",
                "12b736b18d56 • Add topics/a2a .DS_Store and __pycache__ files • 24 file(s)",
            ),
            ("text", "5a6ef2307af1 • Add .gitignore and remove generated files • 2 file(s)"),
            ("text", "b247a11f7c5c • Add A2A travel agent tutorial project • 35 file(s)"),
            ("blank", ""),
            ("muted", "The noisy cache commit is evidence—but not a customer-facing release item."),
        ],
    ),
    (
        "13-render-release-notes",
        "Validated Markdown rendering",
        "Recorded locally · fixed ad43c78..b247a11 walkthrough",
        [
            (
                "prompt",
                "$ python3 repo-release-notes/skills/repo-release-notes/scripts/render_release_notes.py \\",
            ),
            ("prompt", "    --changes changes.json --plan release-plan.json"),
            ("blank", ""),
            ("success", "# Release notes — ad43c78 → b247a11"),
            ("text", "**Audience:** End users"),
            ("blank", ""),
            ("success", "## Summary"),
            (
                "text",
                "The A2A topic is now available, with a runnable travel-agent example and an",
            ),
            ("text", "illustrated walkthrough."),
            ("blank", ""),
            ("success", "## Added"),
            (
                "text",
                "- Complete A2A tutorial, runnable agents, quick start, and illustrations. (`b247a11`)",
            ),
            (
                "text",
                "- Weather-aware packing plans with multiple styles and unit systems. (`b247a11`)",
            ),
            ("text", "- Streamed progress and follow-up prompts for missing details. (`b247a11`)"),
            ("text", "- Deterministic planner plus an optional OpenAI planner. (`b247a11`)"),
            ("blank", ""),
            ("success", "## Fixed"),
            (
                "text",
                "- Corrected the README title’s angle-bracket display. (`fda6360`, `ca3406f`)",
            ),
            ("muted", "Generated/cache artifacts were omitted."),
        ],
    ),
    (
        "14-codex-explicit",
        "Codex · explicit skill activation",
        "Authentic sanitized trace · codex-cli 0.153.4 · read-only",
        [
            ("prompt", "$repo-release-notes Draft end-user release notes for ad43c78..b247a11."),
            ("prompt", "Return Markdown in chat. Do not write, tag, commit, push, or publish."),
            ("blank", ""),
            ("accent", "Codex: I’ll use the repo-release-notes skill to review the Git range and"),
            ("accent", "       draft evidence-backed release notes in chat."),
            ("success", "✓ loaded .agents/skills/repo-release-notes/SKILL.md"),
            ("success", "✓ collect_changes.py exited 0 · 5 commits"),
            ("success", "✓ render_release_notes.py exited 0"),
            ("blank", ""),
            ("text", "# Release notes — ad43c78..b247a11"),
            ("text", "This update adds a weather-aware A2A travel-agent learning project."),
            ("text", "## Added"),
            (
                "text",
                "- A complete tutorial with runnable agents and an illustrated walkthrough. (`b247a11`)",
            ),
            (
                "text",
                "- Weather-aware packing plans, streaming, and follow-up prompts. (`b247a11`)",
            ),
            ("muted", "[final response abridged for the capture]"),
        ],
    ),
    (
        "15-codex-implicit",
        "Codex · implicit skill activation",
        "Authentic sanitized trace · description matched · read-only",
        [
            ("prompt", "Draft end-user release notes for the user-visible changes between ad43c78"),
            ("prompt", "and b247a11. Return them here; do not publish or write a file."),
            ("blank", ""),
            ("accent", "Codex: I’ll use the repo-release-notes skill to review the Git range and"),
            ("accent", "       draft end-user notes here."),
            ("success", "✓ discovered the skill from its frontmatter description"),
            ("success", "✓ loaded SKILL.md and policy · validation exited 0"),
            ("blank", ""),
            ("text", "Release notes for `ad43c78 → b247a11`"),
            (
                "text",
                "**Added**  A2A tutorial, packing plans, streaming, and setup guidance. (`b247a11`)",
            ),
            ("text", "**Fixed**  Corrected the README title’s angle-bracket display. (`fda6360`,"),
            ("text", "             `ca3406f`)"),
            ("blank", ""),
            ("success", "Negative check: a general Git-tag question loaded no skill."),
        ],
    ),
    (
        "16-claude-plugin",
        "Claude Code · plugin discovery boundary",
        "Authentic sanitized stream-json · Claude Code 2.1.68",
        [
            ("prompt", "$ claude -p --plugin-dir ./repo-release-notes \\"),
            (
                "prompt",
                "    '/repo-release-notes:repo-release-notes Draft notes for ad43c78..b247a11'",
            ),
            ("blank", ""),
            ("success", '{"type":"system","subtype":"init","claude_code_version":"2.1.68",'),
            ("success", ' "skills":["repo-release-notes:repo-release-notes"],'),
            ("success", ' "plugins":[{"name":"repo-release-notes"}]}'),
            ("blank", ""),
            ("error", '{"error":"authentication_failed",'),
            ("error", ' "text":"Not logged in · Please run /login"}'),
            ("blank", ""),
            ("muted", "Plugin discovery and namespacing passed. Model execution did not run."),
            ("muted", "Authenticate Claude Code, then repeat the same ephemeral command."),
        ],
    ),
    (
        "17-safe-validation-failure",
        "Failing closed on invented evidence",
        "Recorded locally · renderer created no output",
        [
            (
                "prompt",
                "$ python3 repo-release-notes/skills/repo-release-notes/scripts/render_release_notes.py \\",
            ),
            ("prompt", "    --changes changes.json \\"),
            (
                "prompt",
                "    --plan invalid-release-plan.json --output /tmp/SHOULD_NOT_EXIST.md",
            ),
            ("blank", ""),
            ("error", "error: plan.sections.Fixed[0].commits[0] does not match a collected"),
            ("error", "commit: deadbee"),
            ("text", "exit status: 1"),
            ("prompt", "$ test ! -e /tmp/SHOULD_NOT_EXIST.md && echo 'no output created'"),
            ("success", "no output created"),
            ("blank", ""),
            ("success", "No partial release-notes file was produced."),
        ],
    ),
    (
        "18-final-tests",
        "Deterministic offline verification",
        "Recorded locally · Python 3.13 · pinned development tools",
        [
            ("prompt", "$ .venv/bin/pytest -q"),
            (
                "success",
                "...................................................                      [100%]",
            ),
            ("success", "51 passed"),
            ("blank", ""),
            ("prompt", "$ .venv/bin/ruff check ."),
            ("success", "All checks passed!"),
            ("blank", ""),
            ("prompt", "$ .venv/bin/ruff format --check ."),
            ("success", "15 files already formatted"),
            ("blank", ""),
            ("success", "✓ skill validator   ✓ Codex manifest   ✓ Claude manifest"),
        ],
    ),
]

TERMINAL_COLORS = {
    "prompt": "#55d6a6",
    "text": "#d7e3f4",
    "success": "#70d6ff",
    "accent": "#f9cb6b",
    "muted": "#879ab4",
    "error": "#ff8a80",
    "blank": "#d7e3f4",
}


def card_svg(slug: str, number: str, title: str, subtitle: str) -> str:
    image_data = base64.b64encode((OUTPUT / "layers" / f"{slug}-art.jpg").read_bytes()).decode(
        "ascii"
    )
    subtitle_lines = wrap(subtitle, width=57)
    subtitle_text = "\n".join(
        f'  <text x="80" y="{245 + (index * 36)}" fill="#486581" '
        f'font-family="Inter, Arial, sans-serif" font-size="27" font-weight="400">'
        f"{escape(line)}</text>"
        for index, line in enumerate(subtitle_lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
  width="1600" height="900" viewBox="0 0 1600 900" role="img" aria-labelledby="title description">
  <title id="title">{escape(title)}</title>
  <desc id="description">{escape(subtitle)}</desc>
  <defs>
    <linearGradient id="leftFade" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#f7fafc" stop-opacity="1"/>
      <stop offset="0.35" stop-color="#f7fafc" stop-opacity="0.96"/>
      <stop offset="0.62" stop-color="#f7fafc" stop-opacity="0.20"/>
      <stop offset="1" stop-color="#f7fafc" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="topFade" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f7fafc" stop-opacity="0.96"/>
      <stop offset="0.35" stop-color="#f7fafc" stop-opacity="0.35"/>
      <stop offset="1" stop-color="#f7fafc" stop-opacity="0"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-30%" width="140%" height="170%">
      <feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#102a43" flood-opacity="0.14"/>
    </filter>
  </defs>
  <rect width="1600" height="900" fill="#f7fafc"/>
  <image href="data:image/jpeg;base64,{image_data}"
    x="0" y="0" width="1600" height="900" preserveAspectRatio="xMidYMid slice"/>
  <rect width="1050" height="900" fill="url(#leftFade)"/>
  <rect width="1600" height="330" fill="url(#topFade)"/>
  <g filter="url(#shadow)">
    <rect x="80" y="72" width="170" height="48" rx="24" fill="#1769aa"/>
    <text x="165" y="104" text-anchor="middle" fill="#ffffff"
      font-family="Inter, Arial, sans-serif" font-size="19" font-weight="700" letter-spacing="1.4">SECTION {number}</text>
  </g>
  <text x="80" y="190" fill="#102a43" font-family="Inter, Arial, sans-serif"
    font-size="58" font-weight="750">{escape(title)}</text>
{subtitle_text}
  <g opacity="0.88">
    <rect x="1320" y="824" width="220" height="40" rx="20" fill="#ffffff"/>
    <text x="1430" y="850" text-anchor="middle" fill="#486581"
      font-family="Inter, Arial, sans-serif" font-size="17" font-weight="600">@AdnanMasood</text>
  </g>
</svg>
"""


ARCHITECTURE = """<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900" role="img" aria-labelledby="title description">
  <title id="title">How an agent skill is discovered and used</title>
  <desc id="description">A host catalogs a skill name and description, loads SKILL.md when selected, selectively reads supporting resources, performs the requested work, and verifies the output.</desc>
  <defs>
    <filter id="shadow" x="-15%" y="-20%" width="130%" height="150%">
      <feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#0f2841" flood-opacity="0.12"/>
    </filter>
    <marker id="arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto">
      <path d="M0,0 L12,6 L0,12 z" fill="#1769aa"/>
    </marker>
  </defs>
  <rect width="1600" height="900" fill="#f7fafc"/>
  <text x="80" y="90" fill="#102a43" font-family="Inter, Arial, sans-serif" font-size="47" font-weight="750">How an agent skill actually gets used</text>
  <text x="80" y="137" fill="#486581" font-family="Inter, Arial, sans-serif" font-size="24">Small metadata makes the skill discoverable. The rest arrives only when the task needs it.</text>

  <rect x="245" y="190" width="1110" height="525" rx="36" fill="#eef6fc" stroke="#8abce1" stroke-width="3" stroke-dasharray="12 10"/>
  <text x="285" y="232" fill="#1769aa" font-family="Inter, Arial, sans-serif" font-size="20" font-weight="700" letter-spacing="1.2">HOST-SPECIFIC LOADER</text>
  <rect x="492" y="274" width="615" height="365" rx="30" fill="#ffffff" stroke="#087f8c" stroke-width="3" stroke-dasharray="10 8"/>
  <text x="530" y="316" fill="#087f8c" font-family="Inter, Arial, sans-serif" font-size="20" font-weight="700" letter-spacing="1.2">PORTABLE SKILL BUNDLE</text>

  <g filter="url(#shadow)">
    <rect x="55" y="355" width="170" height="165" rx="26" fill="#ffffff" stroke="#bcccdc" stroke-width="2"/>
    <circle cx="140" cy="407" r="24" fill="#d9e2ec"/>
    <path d="M98 474 C105 437,175 437,182 474" fill="#d9e2ec"/>
    <text x="140" y="505" text-anchor="middle" fill="#102a43" font-family="Inter, Arial, sans-serif" font-size="24" font-weight="700">Request</text>
  </g>

  <g filter="url(#shadow)">
    <rect x="285" y="345" width="180" height="185" rx="26" fill="#ffffff" stroke="#1769aa" stroke-width="3"/>
    <rect x="323" y="382" width="104" height="70" rx="12" fill="#dbeeff"/>
    <circle cx="346" cy="407" r="10" fill="#1769aa"/>
    <rect x="365" y="397" width="42" height="8" rx="4" fill="#1769aa"/>
    <rect x="365" y="416" width="31" height="7" rx="4" fill="#829ab1"/>
    <text x="375" y="488" text-anchor="middle" fill="#102a43" font-family="Inter, Arial, sans-serif" font-size="22" font-weight="700">Catalog</text>
    <text x="375" y="514" text-anchor="middle" fill="#627d98" font-family="Inter, Arial, sans-serif" font-size="16">name + description</text>
  </g>

  <g filter="url(#shadow)">
    <rect x="530" y="350" width="220" height="175" rx="26" fill="#ffffff" stroke="#087f8c" stroke-width="3"/>
    <path d="M582 387 H698 V465 H582 Z" fill="#dff5f3" stroke="#087f8c" stroke-width="2"/>
    <path d="M610 411 H670 M610 430 H682 M610 449 H655" stroke="#087f8c" stroke-width="7" stroke-linecap="round"/>
    <text x="640" y="500" text-anchor="middle" fill="#102a43" font-family="Inter, Arial, sans-serif" font-size="23" font-weight="700">SKILL.md</text>
  </g>

  <g filter="url(#shadow)">
    <rect x="800" y="342" width="270" height="192" rx="26" fill="#ffffff" stroke="#087f8c" stroke-width="3"/>
    <rect x="830" y="382" width="62" height="55" rx="12" fill="#dbeeff"/>
    <circle cx="861" cy="409" r="15" fill="#1769aa"/>
    <rect x="904" y="382" width="62" height="55" rx="12" fill="#dff5f3"/>
    <path d="M921 412 H949" stroke="#087f8c" stroke-width="6"/>
    <rect x="978" y="382" width="62" height="55" rx="12" fill="#ffe5df"/>
    <path d="M993 423 L1009 392 L1025 423 Z" fill="#f27d65"/>
    <text x="935" y="477" text-anchor="middle" fill="#102a43" font-family="Inter, Arial, sans-serif" font-size="22" font-weight="700">Supporting files</text>
    <text x="935" y="505" text-anchor="middle" fill="#627d98" font-family="Inter, Arial, sans-serif" font-size="16">scripts · references · assets</text>
  </g>

  <g filter="url(#shadow)">
    <rect x="1140" y="345" width="175" height="185" rx="26" fill="#ffffff" stroke="#1769aa" stroke-width="3"/>
    <circle cx="1228" cy="411" r="35" fill="#dbeeff"/>
    <path d="M1207 411 L1222 426 L1250 394" fill="none" stroke="#1769aa" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>
    <text x="1228" y="488" text-anchor="middle" fill="#102a43" font-family="Inter, Arial, sans-serif" font-size="22" font-weight="700">Perform</text>
    <text x="1228" y="514" text-anchor="middle" fill="#627d98" font-family="Inter, Arial, sans-serif" font-size="16">the requested work</text>
  </g>

  <g filter="url(#shadow)">
    <rect x="1375" y="355" width="170" height="165" rx="26" fill="#ffffff" stroke="#087f8c" stroke-width="3"/>
    <rect x="1414" y="389" width="92" height="68" rx="12" fill="#dff5f3"/>
    <path d="M1434 423 L1449 438 L1481 404" fill="none" stroke="#087f8c" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
    <text x="1460" y="495" text-anchor="middle" fill="#102a43" font-family="Inter, Arial, sans-serif" font-size="22" font-weight="700">Verify</text>
  </g>

  <path d="M225 438 H276" stroke="#1769aa" stroke-width="5" marker-end="url(#arrow)"/>
  <path d="M465 438 H520" stroke="#1769aa" stroke-width="5" marker-end="url(#arrow)"/>
  <path d="M750 438 H790" stroke="#1769aa" stroke-width="5" marker-end="url(#arrow)"/>
  <path d="M1070 438 H1130" stroke="#1769aa" stroke-width="5" marker-end="url(#arrow)"/>
  <path d="M1315 438 H1365" stroke="#1769aa" stroke-width="5" marker-end="url(#arrow)"/>

  <rect x="270" y="755" width="1060" height="88" rx="22" fill="#102a43"/>
  <text x="800" y="792" text-anchor="middle" fill="#ffffff" font-family="Inter, Arial, sans-serif" font-size="23" font-weight="700">The skill is the reusable procedure—not the model, tool, or host.</text>
  <text x="800" y="823" text-anchor="middle" fill="#c9e6fb" font-family="Inter, Arial, sans-serif" font-size="18">The open format travels; installation, permissions, and invocation remain host decisions.</text>
  <text x="1515" y="862" text-anchor="end" fill="#627d98" font-family="Inter, Arial, sans-serif" font-size="17" font-weight="600">@AdnanMasood</text>
</svg>
"""


def terminal_svg(
    title: str,
    subtitle: str,
    lines: list[tuple[str, str]],
) -> str:
    line_height = min(34, 600 // max(len(lines), 1))
    first_line_y = 184
    text_nodes = []
    for index, (kind, content) in enumerate(lines):
        text_nodes.append(
            f'<text x="98" y="{first_line_y + index * line_height}" '
            f'fill="{TERMINAL_COLORS[kind]}" font-family="SFMono-Regular, Menlo, Consolas, '
            f'monospace" font-size="21">{escape(content)}</text>'
        )
    transcript = "\n    ".join(text_nodes)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900" role="img" aria-labelledby="title description">
  <title id="title">{escape(title)}</title>
  <desc id="description">{escape(subtitle)}. The adjacent article text contains a complete accessible transcript.</desc>
  <defs>
    <filter id="shadow" x="-10%" y="-10%" width="120%" height="130%">
      <feDropShadow dx="0" dy="12" stdDeviation="18" flood-color="#020812" flood-opacity="0.35"/>
    </filter>
    <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#071321"/>
      <stop offset="1" stop-color="#0c2036"/>
    </linearGradient>
  </defs>
  <rect width="1600" height="900" fill="url(#background)"/>
  <g filter="url(#shadow)">
    <rect x="48" y="42" width="1504" height="816" rx="25" fill="#0b1829" stroke="#29435f" stroke-width="2"/>
    <path d="M48 126 H1552" stroke="#29435f" stroke-width="2"/>
  </g>
  <circle cx="82" cy="84" r="10" fill="#ff625d"/>
  <circle cx="116" cy="84" r="10" fill="#ffbd45"/>
  <circle cx="150" cy="84" r="10" fill="#27c93f"/>
  <text x="800" y="82" text-anchor="middle" fill="#d7e3f4" font-family="Inter, Arial, sans-serif" font-size="25" font-weight="700">{escape(title)}</text>
  <text x="800" y="109" text-anchor="middle" fill="#879ab4" font-family="Inter, Arial, sans-serif" font-size="16">{escape(subtitle)}</text>
  <g>
    {transcript}
  </g>
  <rect x="1220" y="806" width="286" height="31" rx="15" fill="#102a43" stroke="#29435f"/>
  <text x="1363" y="827" text-anchor="middle" fill="#9fb3c8" font-family="Inter, Arial, sans-serif" font-size="14" font-weight="600">SANITIZED RECORDED OUTPUT</text>
</svg>
"""


def transcript_text(title: str, subtitle: str, lines: list[tuple[str, str]]) -> str:
    visible_lines = [content for _, content in lines]
    return f"{title}\n{subtitle}\n\n" + "\n".join(visible_lines) + "\n"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "00-architecture.svg").write_text(ARCHITECTURE, encoding="utf-8")
    for slug, number, title, subtitle in CARDS:
        (OUTPUT / f"{slug}.svg").write_text(
            card_svg(slug, number, title, subtitle), encoding="utf-8"
        )
    transcripts = OUTPUT / "transcripts"
    transcripts.mkdir(exist_ok=True)
    for slug, title, subtitle, lines in CAPTURES:
        (OUTPUT / f"{slug}.svg").write_text(terminal_svg(title, subtitle, lines), encoding="utf-8")
        (transcripts / f"{slug}.txt").write_text(
            transcript_text(title, subtitle, lines), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
