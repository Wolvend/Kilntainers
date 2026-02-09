# Kilntainers: Secure Agent Sandboxes

Kilntainers is an MCP server that gives LLM agents isolated Linux sandboxes for executing shell commands. It exposes a single tool — `shell_exec` — providing the full power of a Linux command line in an ephemeral, secure environment.

## Overview

 - MCP: a simple MCP server with a single too: `sandbox_exec`, allowing your agent to run powerful linux commands
 - Secure: your agent communicates with your sandbox, doesn't run in inside it. There's no need for API keys or secrets in the sandbox.
 - Multiple backends: run sandboxes with containers (Docker, Podman), remote VMs (Modal, E2B), Linux in WASM (container2wasm), or lightweight WASM BusyBox 

## Why? Power + Security

Coding agents like claude code have shown us how powerful an agent with a terminal can be. Agents are already excellent at using terminals (decades of pre-training data), and can save thousands of tokens by using common linux utilties (grep, find, jq, tail, etc).

However: agents with a terminal is generally a security nightmare. Options are emerging for software development agents (Claude Code sandboxes, Codex Cloud, etc). However, there are fewer options targeting agent development and agent deployment. We want a system to run potentially thousands of parallel agents with their own isolated containers, while providing an excellent and reliable local agent development UX.

 - Developer tools: Docker, Podman, WASM, etc
 - Deployment tools: Modal, E2B Cloud, Hosted Docker, WASM, etc

## Specs

This repository has detailed specs. Key design files include:

 - [specs/functional_spec.md] External behavior — the MCP tool interface, server configuration, connection lifecycle, backend behavioral contract, and security model. Not an architecture or implementation document.
- [specs/decisions.md](decisions.md) — design decisions with rationale (source of truth). However these are generally captured in functional_spec
- [specs/project_overview.md](project_overview.md) — original motivation and vision, but not maintained. Functional spec is generally more complete and up to date.
- [specs/spec_queue.md](spec_queue.md) — tracking of items requiring specification
