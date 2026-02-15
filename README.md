# Kilntainers: Secure Agent Sandboxes

[![Build and Test](https://github.com/Kiln-AI/kilntainers/actions/workflows/build_and_test.yml/badge.svg)](https://github.com/Kiln-AI/kilntainers/actions/workflows/build_and_test.yml) [![Format and Lint](https://github.com/Kiln-AI/kilntainers/actions/workflows/format_and_lint.yml/badge.svg)](https://github.com/Kiln-AI/kilntainers/actions/workflows/format_and_lint.yml) 

Kilntainers is an [MCP server](https://modelcontextprotocol.io/) that gives LLM agents isolated Linux sandboxes for executing shell commands. It exposes a single tool — `sandbox_exec` — providing the full power of a Linux command line in an ephemeral, secure environment.

Designed for both development and production, Kilntainers supports local containers (Docker, Podman), cloud VMs (Modal.com), and lightweight WASM sandboxes — scaling from a single agent on your laptop to thousands in parallel.

## Why Kilntainers?

Coding agents like Claude Code have shown how powerful an agent with a terminal can be. Agents are already excellent at using terminals, and can save thousands of tokens by leveraging common Linux utilities like `grep`, `find`, `jq`, `awk`, etc. But giving an agent access to the host OS is a security nightmare. Kilntainers gives every agent its own isolated, ephemeral sandbox.

## Quick Start

Install and run:

```bash
uv tool install kilntainers
kilntainers  # starts a stdio MCP server with Docker + debian-slim
```

Add to your MCP client (Claude Desktop, Cursor, etc.):

```json
{
  "mcpServers": {
    "kilntainers": {
      "command": "kilntainers"
    }
  }
}
```

## How It Works

```
┌─────────────┐     MCP     ┌──────────────┐      ┌───────────────────┐
│  LLM Agent  │◄───────────►│  Kilntainers │◄────►│  Sandbox          │
│  (client)   │             │  MCP Server  │      │  (container/WASM) │
└─────────────┘             └──────────────┘      └───────────────────┘
```

1. An MCP client connects to Kilntainers
2. On the first `sandbox_exec` call, Kilntainers creates an isolated sandbox
3. Commands run inside the sandbox; stdout, stderr, and exit code are returned
4. When the session ends, the sandbox is destroyed and resources are cleaned up. Each connection gets its own independent sandbox.

**Security** The agent communicates *with* the sandbox over MCP — it doesn't run *inside* it. This is intentional: agents often need secrets (API keys, system prompts, code), and those should never be exposed inside a sandbox where a prompt injection could exfiltrate them.

## Backends

### Docker and Podman (default)

Local containers via Docker or Podman. Any OCI image works.

```bash
kilntainers                                     # Docker + debian-slim (defaults)
kilntainers --image=alpine --engine=podman      # Podman + Alpine
kilntainers --image=node:22 --network           # Node.js with networking
```

### Modal.com — Cloud VMs

Hosted VMs with sub-second startup via [Modal.com](https://modal.com). Scales to thousands of parallel sandboxes. Supports GPUs.

```bash
kilntainers --backend=modal
kilntainers --backend=modal --gpu=A10G --region=us-east  # GPU-accelerated
```

Authenticate via `modal setup` CLI or `--modal-token-id` / `--modal-token-secret` flags.

### WASM BusyBox

Runs [go-busybox](https://github.com/rcarmo/go-busybox) in a WebAssembly sandbox. Not a full Linux environment, but provides common utilities (`grep`, `awk`, `sed`, `ls`, etc.) in a very lightweight package.

```bash
uv tool install kilntainers[wasm]  # WASM support is an optional dependency (+15MB)
kilntainers --backend=go_busybox
```

### WASM Runner

Run a custom WASM module as the sandbox backend. Useful for providing agents with specific tools compiled to WebAssembly.

```bash
kilntainers --backend=wasm --wasm-path=./my_tool.wasm
```

## Installation

```bash
uv tool install kilntainers        # recommended
uv tool install kilntainers[wasm]  # optional, include WASM backends (+15MB)
pip install kilntainers            # also works with pip
```

Requires Python 3.13+. Docker backend requires Docker or Podman. Modal backend requires a [Modal.com](https://modal.com) account.

<details>
<summary><h2>CLI Reference</h2></summary>

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

</details>
