# Kilntainers: Secure Agent Sandboxes

Kilntainers is an MCP server that gives LLM agents isolated Linux sandboxes for executing shell commands. It exposes a single tool — `shell_exec` — providing the full power of a Linux command line in an ephemeral, secure environment.

## Specs

This repository has detailed specs, and follows spec-driven design. Key files include:

- [specs/functional_spec.md] External behavior — the MCP tool interface, server configuration, connection lifecycle, backend behavioral contract, and security model. Not an architecture or implementation document.
- [specs/architecture/architecture_summary.md] A summary of architecture docs covering detailed technical designs. Additional architecture docs live in this path.
- [specs/decisions.md](decisions.md) — design decisions with rationale (source of truth). However these are generally captured in functional_spec and architecture, and does not need to be reviewed.
- [specs/project_overview.md](project_overview.md) — original motivation and vision, but not maintained. Functional spec is generally more complete and up to date. Do not review unless looking for original motivation.
- [specs/spec_queue.md](spec_queue.md) — tracking of items requiring specification

## Commands & Tools

You have access ot a MCP server to running tools like lint, format, types, test.

We use:
 - ruff for formatting: `uvx ruff format` to fix issues
 - ruff checking: `uvx ruff check --fix` to fix issues
 - ty for typechecking: `uvx ty check` to run

This script will run all checks (lint, format, types, tests):
```bash
uv run ./checks.sh
```

Run tests only:
```bash
uv run pytest .
```
