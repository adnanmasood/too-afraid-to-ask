# Contributing to Too Afraid to Ask

Thanks for helping make intimidating technology approachable. A contribution can improve an existing explanation, fix an example, or add a complete new topic.

## The standard for a topic

A topic should let a newcomer answer four questions:

1. What problem does this technology solve?
2. What are the few components I need to keep in my head?
3. How do I run one complete example and see real output?
4. What would I need to change before using it in production?

Prefer one understandable end-to-end path over a collection of disconnected features. Define unfamiliar terms when they first appear, show boundaries between components, and state clearly when AI is optional rather than intrinsic to the technology.

## Create a topic

Use a short, descriptive, kebab-case slug:

```text
topics/mcp/
topics/a2a-agents/
topics/vector-databases/
```

Copy the technology-neutral scaffold, then replace its placeholders:

```bash
cp -R templates/topic topics/your-topic
```

The minimum useful topic contains a `README.md`. A complete runnable topic will normally also contain:

```text
topics/your-topic/
├── README.md                 # Concise overview and quick start
├── docs/
│   ├── tutorial.md           # Long-form learning path
│   └── images/               # Topic-owned diagrams and screenshots
├── source-or-example-code/   # Layout appropriate to its language
├── dependency-manifest       # Owned by this topic
└── tests/                    # Owned and configured by this topic
```

Do not add a language-specific manifest at the series root. Each topic must document the directory from which its install, run, and test commands execute.

## Documentation checklist

- State the audience, prerequisites, learning goals, and finished result near the beginning.
- Give the reader a small mental model and glossary before introducing framework details.
- Keep the README concise and put the complete reconstruction in the topic's `docs/` directory.
- Use focused, copyable snippets and link to complete source files where appropriate.
- Add checkpoints so readers can confirm that each major stage works.
- Give every image meaningful alt text and a nearby text transcript when the image contains terminal output.
- Label live values and timestamps as point-in-time examples.
- End with security, privacy, troubleshooting, next exercises, and authoritative further reading.
- Update the topic table in the repository README when the topic becomes available.

## Code and dependency rules

- Keep source, tests, dependency manifests, environment examples, and tooling inside the topic.
- Pin teaching-project dependencies when reproducibility matters, and explain intentional exceptions.
- Never commit API keys, access tokens, personal paths, usernames, customer data, or populated `.env` files.
- Make local-safe defaults explicit. Clearly label authentication, deployment, rate limiting, persistence, and other production concerns that the sample omits.
- Attribute external data and libraries where their licenses or usage terms require it.

## Tests and captured output

Normal tests should be deterministic and offline. Mock paid or mutable external services, and make any live smoke test explicitly opt-in. Document all test, lint, and formatting commands in the topic README and run them from the topic directory before contributing.

Screenshots must come from real runs. Sanitize secrets, usernames, machine-specific paths, process identifiers, and other personal details. Include a copyable transcript beside terminal screenshots so the guide remains searchable and accessible.

## Sources

Prefer specifications, official documentation, original research, and provider documentation. Link claims about protocols or fast-changing APIs directly to those authoritative sources, note the versions used by the example, and distinguish sourced facts from project-specific design choices.
