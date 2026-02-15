# Kilntainers: Secure Agent Sandboxes

Kilntainers is an MCP server that gives LLM agents isolated Linux sandboxes for executing shell commands. It exposes a single tool — `sandbox_exec` — providing the full power of a Linux command line in an ephemeral, secure environment.

It can use a variety of sandboxing backends including local containers (Docker, Podman), Cloud VMs (Modal.com), and even a Web Assembly BusyBox.

It's designed for both development and deployment, giving each agent its own ephemeral sandbox and scaling to thousands of parallel sandboxes.

## Overview

 - Simple MCP Interface: Kilntainers exposes a single tool: `sandbox_exec`, allowing your agent to run any Linux commands
 - Secure: your agent communicates with your sandbox, doesn't run inside it. There's no need for API keys or secrets in the sandbox.
 - Ephemeral sandboxes: each agent gets its own container for the duration of its MCP session, after which the resources are shut down and cleaned up.
 - Multiple backends: Docker, Podman, Modal.com (hosted VMs with sub-second startup), WASM BusyBox (lightweight tools like ls, grep, awk, etc), and a WASM Runner.

## Quick Start

```bash
uv install tool kilntainers # we suggest the UV package manager
kilntainers # starts a stdio MCP server with Docker and debian-slim
```

## Security Model: Containers with Sandboxes, not Containers in Sandboxes

A key note about the architecture: Kilntainers gives your agent access to a sandbox over MCP, it doesn't run your agent inside a sandbox. This is an intentional design choice. Agents often need secrets (API keys, system prompts, code), and we don't want that exposed inside the sandbox where a prompt injection attack could exfiltrate it.

You may separately want to sandbox your agents, which is fine.

## Backends: 

Kilntainers supports a number of sandbox backends:

 - Docker [default]: Everyone's favourite container manager. Install Docker Desktop, and Kilntainers will automatically start a debian-slim container when connected.
 - Podman: Docker's open source cousin. Just set `--engine=podman` on Kilntainer's CLI options.
 - Modal.com: cloud hosted VMs with sub-second startup. Set `--backend=modal`. Authenticate with API keys or `modal setup` CLI.
 - WASM BusyBox: runs the [go-busybox](https://github.com/rcarmo/go-busybox) project in a WASM (WebAssembly) sandbox. Not a full unix container/VM, but provides common utilities like grep, awk, and more in a very lightweight sandbox. Specify `--backend=go_busybox` on startup.
 - WASM Runner: run a custom WASM module (details below)
 - Extensible: add custom backends


## Usage Examples

Install
```bash
uv tool install kilntainers # standard install
uv tool install kilntainers[wasm] # install with WASM (+15MB)
```

Docker Debian-slim with stdio MCP server:
```bash
kilntainers
```

Podman running Alpine Linux on HTTP server
```bash
kilntainers --image=alpine --transport=http --engine=podman
```

Modal.com Hosted VMs
```bash
kilntainers --backend=modal --modal-token-id=1234 --modal-token-secret=ABCD
```

BusyBox with WASM Sandboxing
```bash
kilntainers --backend=go_busybox
```

## CLI Reference

```
usage: kilntainers [-h] [--backend {docker,go_busybox,modal,wasm}] [--transport {stdio,http}] [--host HOST] [--port PORT] [--timeout TIMEOUT]
                   [--output-limit OUTPUT_LIMIT] [--session-timeout SESSION_TIMEOUT] [--shell SHELL] [--network]
                   [--tool-instruction-override TOOL_INSTRUCTION_OVERRIDE] [--extended-tool-instruction EXTENDED_TOOL_INSTRUCTION] [--engine ENGINE]
                   [--docker-host DOCKER_HOST] [--image IMAGE] [--cpu CPU] [--memory MEMORY] [--docker-run-flag DOCKER_RUN_FLAGS] [--modal-token-id MODAL_TOKEN_ID]
                   [--modal-token-secret MODAL_TOKEN_SECRET] [--modal-app-name MODAL_APP_NAME] [--modal-cpu MODAL_CPU] [--modal-memory MODAL_MEMORY] [--gpu GPU]
                   [--region REGION] [--sandbox-timeout SANDBOX_TIMEOUT] [--wasm-path WASM_PATH] [--wasm-max-memory WASM_MAX_MEMORY] [--wasm-fuel WASM_FUEL]

MCP server providing isolated Linux sandboxes for LLM agent shell execution.

options:
  -h, --help            show this help message and exit

core options:
  --backend {docker,go_busybox,modal,wasm}
                        Backend to use (default: docker). Available: docker, go_busybox, modal, wasm
  --transport {stdio,http}
                        MCP transport (default: stdio)
  --host HOST           HTTP bind address (default: 127.0.0.1, HTTP mode only)
  --port PORT           HTTP listen port (default: 8435, HTTP mode only)
  --timeout TIMEOUT     Default exec timeout in seconds (default: 120)
  --output-limit OUTPUT_LIMIT
                        Max combined stdout+stderr bytes per exec (default: 2097152 = 2 MiB)
  --session-timeout SESSION_TIMEOUT
                        Idle session timeout in seconds (default: 300, HTTP mode only)
  --shell SHELL         Shell binary for command mode (e.g., /bin/bash, ash). Default: /bin/bash.
  --network             Enable network access in sandboxes (default: disabled)

tool description:
  --tool-instruction-override TOOL_INSTRUCTION_OVERRIDE
                        Replace the entire sandbox_exec tool description
  --extended-tool-instruction EXTENDED_TOOL_INSTRUCTION
                        Append to the backend's default tool description

docker backend options:
  --engine ENGINE       Container CLI binary (default: docker). Supports podman.
  --docker-host DOCKER_HOST
                        Docker daemon socket/address, passed as -H to the Docker CLI (e.g., "ssh://user@remote-host", "tcp://host:2375")
  --image IMAGE         Docker image (default: debian:bookworm-slim)
  --cpu CPU             Docker CPU limit (e.g., "1.5")
  --memory MEMORY       Docker memory limit (e.g., "512m")
  --docker-run-flag DOCKER_RUN_FLAGS
                        Additional flag passed to docker run. Repeatable. (e.g., --docker-run-flag "--pids-limit=256")

modal backend options:
  --modal-token-id MODAL_TOKEN_ID
                        Modal token ID (overrides environment/default auth)
  --modal-token-secret MODAL_TOKEN_SECRET
                        Modal token secret (overrides environment/default auth)
  --modal-app-name MODAL_APP_NAME
                        Modal app name (default: kilntainers)
  --modal-cpu MODAL_CPU
                        CPU cores (fractional, default: 1.0)
  --modal-memory MODAL_MEMORY
                        Memory in MiB (default: 512)
  --gpu GPU             GPU type (e.g., "A10G", "H100")
  --region REGION       Geographic region (e.g., "us-east")
  --sandbox-timeout SANDBOX_TIMEOUT
                        Sandbox lifetime timeout in seconds (default: 3600, max 86400)

wasm backend options:
  --wasm-path WASM_PATH
                        Path to the .wasm file to execute (required for wasm backend)
  --wasm-max-memory WASM_MAX_MEMORY
                        Max WASM memory in MiB (default: 256)
  --wasm-fuel WASM_FUEL
                        WASM instruction fuel limit (default: unlimited)
```

## Motivation: Deploying Agent Systems

Coding agents like Claude Code have shown us how powerful an agent with a terminal can be. Agents are already excellent at using terminals (decades of pre-training data). They can save thousands of tokens by using common Linux utilities like grep, find, jq, tail, awk, etc. They can perform tasks that LLMs don't excel at (like math) using CLI utilities and scripts.

However: agents with access to the host OS is generally a security nightmare. Sandboxes exist for software development agents (Claude Code sandboxes, Codex Cloud, etc). However, there are fewer options for deploying your own agents. 

Kilntainers is a system that can scale to thousands of parallel agents, each with their own isolated containers, while also providing a reliable local agent development UX (Docker, Podman).

## Specs

This repository has detailed specs. Key design files include:

 - [specs/functional_spec.md] External behavior — the MCP tool interface, server configuration, connection lifecycle, backend behavioral contract, and security model. Not an architecture or implementation document.
- [specs/decisions.md](decisions.md) — design decisions with rationale (source of truth). However these are generally captured in functional_spec
- [specs/project_overview.md](project_overview.md) — original motivation and vision, but not maintained. Functional spec is generally more complete and up to date.
- [specs/spec_queue.md](spec_queue.md) — tracking of items requiring specification
