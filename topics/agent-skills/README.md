# Agent Skills and `SKILL.md`: Too Afraid to Ask

Agent Skills package repeatable know-how for AI agents. A skill is a directory whose required entry
point is `SKILL.md`: compact discovery metadata in YAML frontmatter, procedural instructions in
Markdown, and optional scripts, references, and assets loaded only when needed.

This topic provides a portable, offline example called **Repo Release Notes**. It collects facts from
an explicit local Git range, asks the agent to create an evidence-linked editorial plan, validates
that plan, and renders consistent Markdown. The sample shows the useful boundary between agent
judgement and deterministic code without requiring an API key or a network connection.

> Start with the illustrated, step-by-step tutorial:
> [Build Your First Agent Skill](docs/build-your-first-agent-skill.md).

The [Medium-ready draft](docs/medium-draft.md) is the publication-length, judgement-free companion:
it covers the open format, progressive disclosure, Codex and Claude in depth, a concise host matrix,
distribution, portability, security, hosted APIs, and evaluation.

## The mental model

A skill is a field manual for an already-capable agent:

```text
user request
    │
    ▼
agent host ── sees name + description in a compact catalog
    │
    ├── request matches ──► reads SKILL.md
    │                           │
    │                           ├── runs scripts when exact behavior helps
    │                           ├── reads references when policy/detail helps
    │                           └── reuses assets when output consistency helps
    │
    ▼
reviewable result within the host's existing permissions
```

The skill teaches the procedure. It does not provide a new credential, tool, sandbox, model, or
security identity. Those remain the responsibility of Codex, Claude Code, or another compatible
host.

This also distinguishes skills from nearby concepts:

| Layer | Short version |
|---|---|
| `SKILL.md` | Agent ↔ reusable procedural know-how |
| MCP | Agent ↔ tool or data source |
| A2A | Agent ↔ independent agent |
| Plugin | Distribution bundle that may include skills, MCP servers, hooks, and metadata |

## What the example does

The Repo Release Notes workflow separates facts, judgement, and rendering:

1. `collect_changes.py` resolves the exact base and head refs and emits structured Git evidence.
2. The agent treats commit content as untrusted data, reads the packaged release-notes policy, and
   drafts a release-plan JSON document.
3. Every proposed release-note item cites one or more collected commit SHAs.
4. `render_release_notes.py` rejects unknown or ambiguous evidence and renders the approved template.
5. Tests check deterministic behavior, including safe failures.
6. The workflow stops at a draft. It does not publish, tag, commit, or push.

![Agent Skills architecture from catalog discovery to activated instructions and on-demand resources](docs/images/agent-skills-tutorial/00-architecture.png)

## Quick start

Requirements:

- Python 3.11 or newer
- Git on `PATH`
- no API key and no network access for the example runtime

From the repository root:

```bash
cd topics/agent-skills
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Windows PowerShell users can activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

Validate the portable skill package:

```bash
agentskills validate repo-release-notes/skills/repo-release-notes
```

The pinned Python distribution is named `skills-ref==0.1.1`; the console command it installs is
`agentskills`.

Collect the repository range used by the tutorial and write the JSON to standard output:

```bash
python repo-release-notes/skills/repo-release-notes/scripts/collect_changes.py \
  --repo ../.. \
  --base ad43c78 \
  --head b247a11
```

Add `--output PATH` to persist the snapshot. The renderer consumes both that evidence and an
agent-authored plan:

```bash
python repo-release-notes/skills/repo-release-notes/scripts/render_release_notes.py \
  --changes tests/fixtures/changes.json \
  --plan tests/fixtures/release-plan.json
```

It uses the packaged template by default. Override it or write an output file only when needed:

```bash
python repo-release-notes/skills/repo-release-notes/scripts/render_release_notes.py \
  --changes tests/fixtures/changes.json \
  --plan tests/fixtures/release-plan.json \
  --template repo-release-notes/skills/repo-release-notes/assets/release-notes-template.md \
  --output /tmp/release-notes.md
```

## Package layout

Here, `.` means `topics/agent-skills`:

```text
.
├── README.md
├── requirements-dev.txt
├── pyproject.toml
├── docs/
│   ├── build-your-first-agent-skill.md
│   ├── medium-draft.md
│   └── images/
│       └── agent-skills-tutorial/
├── repo-release-notes/
│   ├── .claude-plugin/
│   │   └── plugin.json
│   ├── .codex-plugin/
│   │   └── plugin.json
│   └── skills/
│       └── repo-release-notes/
│           ├── SKILL.md
│           ├── agents/
│           │   └── openai.yaml
│           ├── assets/
│           │   └── release-notes-template.md
│           ├── references/
│           │   └── release-notes-policy.md
│           └── scripts/
│               ├── collect_changes.py
│               └── render_release_notes.py
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── changes.json
    │   └── release-plan.json
    ├── golden/
    │   └── release-notes.md
    ├── test_collect_changes.py
    ├── test_render_release_notes.py
    └── test_skill_structure.py
```

The portable unit is `repo-release-notes/skills/repo-release-notes`. The Codex and Claude plugin
manifests wrap that same directory; they do not duplicate the procedure. `agents/openai.yaml` is an
OpenAI adapter for interface and policy metadata. Essential behavior remains in `SKILL.md` so other
hosts can use it.

## Use it from an agent host

Discovery and invocation are host-specific; neither is defined by the open Agent Skills format.

For Codex, expose the portable directory through a supported skill location such as
`.agents/skills/repo-release-notes`, restart the session, inspect `/skills`, and invoke it explicitly:

```text
$repo-release-notes Draft end-user release notes for ad43c78..b247a11. Return
Markdown in chat. Do not write, tag, commit, push, or publish.
```

The captured `codex-cli 0.153.4` run announced the selected skill, loaded
`.agents/skills/repo-release-notes/SKILL.md`, ran the collector successfully, and returned four
Added bullets citing `b247a11`. The implicit request below selected the same skill and returned the
A2A additions plus a README-title fix:

```text
Draft end-user release notes for the user-visible changes between ad43c78 and
b247a11. Return them here; do not publish or write a file.
```

A negative check asking only for the difference between a Git tag and branch produced an ordinary
Git explanation with no skill references. That confirms both sides of routing: relevant release
work activated the skill; adjacent general Git education did not.

For Claude Code, place or link it at `.claude/skills/repo-release-notes`, or install the containing
Claude plugin. A plugin-bundled skill uses the namespaced command:

```text
/repo-release-notes:repo-release-notes ad43c78..b247a11
```

Claude Code 2.1.68 authentically loaded the local plugin and discovered the namespaced
`repo-release-notes:repo-release-notes` skill. The captured machine was not authenticated
(`loggedIn: false`), so inference stopped with `Not logged in · Please run /login`; no Claude release
notes were generated. An implicit retry reached the same initialized catalog and auth boundary. Run
`claude auth login` (or `/login` interactively), verify
`claude auth status --json` reports `loggedIn: true`, and retry. Compatible hosts can also select a
skill implicitly from its frontmatter description, but test explicit use, implicit use, and
non-activation in the exact authenticated product surface and version you intend to support.

## Offline verification

The normal verification path is deterministic and makes no network or paid model request. From
`topics/agent-skills`, run exactly:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
agentskills validate repo-release-notes/skills/repo-release-notes
pytest
ruff check .
ruff format --check .
```

The validator proves that the package shape and portable frontmatter conform to the standard. The
test suite covers script behavior. Neither replaces review of instructions, dependencies,
permissions, or organizational policy.

## Production boundary

This is a teaching package and a safe drafting workflow, not a release-publishing system. It reads
local Git objects and writes only when an explicit output path is supplied. It deliberately does
not:

- authenticate to GitHub, GitLab, a package registry, or a deployment service;
- push commits or tags;
- publish a release or changelog;
- decide that a security-sensitive statement is safe to disclose;
- approve breaking-change language on behalf of maintainers; or
- turn repository text into trusted instructions.

Before adapting it to production, pin and review the skill version, protect credentials outside the
package, run with minimum permissions, treat commits and diffs as untrusted evidence, test hostile
inputs, define an approved output location, and keep publication behind explicit human or policy
approval. If a host-specific field such as `allowed-tools` suppresses permission prompts, review it
as an authority grant rather than a portability convenience.

The durable design rule is:

> Collect facts deterministically, apply judgement explicitly, validate every claim, and keep
> consequential execution outside the skill boundary.

## Further reading

- [Agent Skills specification](https://agentskills.io/specification)
- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills)
- [OpenAI: Skills in the API](https://developers.openai.com/api/docs/guides/tools-skills)
- [Anthropic: Extend Claude with skills](https://code.claude.com/docs/en/skills)
- [Anthropic: Agent Skills API guide](https://platform.claude.com/docs/en/build-with-claude/skills-guide)
