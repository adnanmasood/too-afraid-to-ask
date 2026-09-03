# Too Afraid to Ask <Tech>

**Too Afraid to Ask** is a collection of small, runnable projects for technologies that can feel harder to approach than they really are. Each topic begins with the mental model, builds one complete example, and shows the real requests and responses moving through it.

The guides assume curiosity, not prior expertise. They keep the first implementation deliberately narrow so you can understand the whole system before adding production complexity.

## Topics

| Topic | Status | Start here |
|---|---|---|
| Model Context Protocol (MCP) | Available | [Project overview](topics/mcp/README.md) · [Illustrated tutorial](topics/mcp/docs/build-your-first-weather-mcp-server.md) |
| Agent2Agent (A2A) agents | Planned | Coming next |

## Try the MCP project

Every topic is an independent project. Enter its directory before installing dependencies or running commands:

```bash
cd topics/mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows PowerShell users can activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

Then follow the [MCP quick start](topics/mcp/README.md) or work through the [complete beginner tutorial](topics/mcp/docs/build-your-first-weather-mcp-server.md).

## How the repository is organized

```text
.
├── README.md                 # Series catalog
├── CONTRIBUTING.md          # How to add or improve a topic
├── templates/topic/         # Technology-neutral writing scaffold
└── topics/
    └── mcp/                 # Complete, independently runnable MCP project
```

Each directory under `topics/` owns its README, documentation, images, source code, dependency files, and tests. There is intentionally no repository-wide Python package or shared runtime: a future JavaScript, Java, .NET, or mixed-language topic should fit without inheriting MCP's toolchain.

## Add another topic

Read [CONTRIBUTING.md](CONTRIBUTING.md), copy the scaffold in [`templates/topic/`](templates/topic/README.md), and create a kebab-case directory such as `topics/a2a-agents/`. Add the finished topic to the table above; do not create empty topic directories as placeholders.
