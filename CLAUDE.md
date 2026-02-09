# Kilntainers: Secure Agent Sandboxes

Kilntainers is an MCP server that gives LLM agents isolated Linux sandboxes for executing shell commands. It exposes a single tool — `shell_exec` — providing the full power of a Linux command line in an ephemeral, secure environment.

## Specs

This repository has detailed specs, and follows spec-driven design. Key files include:

 - [specs/functional_spec.md] External behavior — the MCP tool interface, server configuration, connection lifecycle, backend behavioral contract, and security model. Not an architecture or implementation document.
- [specs/decisions.md](decisions.md) — design decisions with rationale (source of truth). However these are generally captured in functional_spec
- [specs/project_overview.md](project_overview.md) — original motivation and vision, but not maintained. Functional spec is generally more complete and up to date.
- [specs/spec_queue.md](spec_queue.md) — tracking of items requiring specification
