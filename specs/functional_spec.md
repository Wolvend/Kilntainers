# Kilntainers — Functional Specification

## 1. Overview

Kilntainers is an MCP server that gives LLM agents isolated Linux sandboxes for executing shell commands. It exposes a single tool — `shell_exec` — providing the full power of a Linux command line in an ephemeral, secure environment.

**Scope of this document:** External behavior — the MCP tool interface, server configuration, connection lifecycle, backend behavioral contract, and security model. Not an architecture or implementation document.

**Companion documents:**

- [decisions.md](decisions.md) — design decisions with rationale (source of truth)
- [project_overview.md](project_overview.md) — motivation and vision
- [spec_queue.md](spec_queue.md) — tracking of items requiring specification

**V1 scope:** Docker is the only backend. The backend interface is designed for pluggability so additional backends (Modal, E2B, WASI, etc.) can be added without changing the MCP layer. (D2)

**Transport:** Kilntainers supports stdio and Streamable HTTP transports. The legacy SSE transport is not supported — SSE and Streamable HTTP are different MCP transports, and SSE is deprecated. (D8)

---

## 2. MCP Tool: `shell_exec`

Kilntainers exposes exactly one MCP tool: `shell_exec`. (D9)

### 2.1 Input Schema

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string",
      "description": "Shell command to execute. Supports full shell syntax including pipes, redirects, and command chaining. Mutually exclusive with args."
    },
    "args": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Command and arguments as an array. The first element is the executable; remaining elements are arguments passed directly with no shell interpretation. Mutually exclusive with command."
    },
    "working_directory": {
      "type": "string",
      "description": "Absolute path to the working directory. Defaults to the sandbox's configured working directory."
    },
    "timeout": {
      "type": "integer",
      "minimum": 1,
      "description": "Maximum execution time in seconds. Overrides the server-configured default."
    }
  },
  "oneOf": [
    { "required": ["command"] },
    { "required": ["args"] }
  ]
}
```

**Parameter details:**

- **`command`** (string) — A shell command string. The backend executes this through its shell (e.g., `bash -c` for Docker/Debian). Supports pipes (`|`), redirects (`>`, `>>`), chaining (`&&`, `;`), subshells, globbing, and variable expansion. (D15, D20)

- **`args`** (string array) — The first element is the executable path or name; remaining elements are arguments. No shell is involved — arguments are passed directly to the process via exec. No pipes, redirects, globbing, or variable expansion. Use for programmatic calls where exact argument integrity matters (e.g., passing strings with special characters to a text editor tool). (D15)

- **`working_directory`** (string, optional) — Absolute path within the sandbox. Default: the sandbox's working directory as configured by the image (e.g., Dockerfile `WORKDIR`), falling back to `/` if not set. (D13)

- **`timeout`** (integer, optional) — Seconds. Overrides the server-configured default for this call only. No enforced maximum — practical limits depend on the MCP client's own timeout configuration. (D21, D25)

**Validation:** Exactly one of `command` or `args` must be provided. Both present → MCP error. Neither present → MCP error. If `working_directory` is provided, it must be an absolute path.

### 2.2 Response Format

The response is a JSON object returned as a text content block:

```json
{
  "stdout": "hello world\n",
  "stderr": "",
  "exit_code": 0,
  "exec_duration_ms": 45
}
```

| Field | Type | Description |
|---|---|---|
| `stdout` | string | Standard output from the command. Empty string if none. |
| `stderr` | string | Standard error. May contain infrastructure messages (timeout, output limit). |
| `exit_code` | integer | Process exit code, or a synthetic code for infrastructure events (124 for timeout). |
| `exec_duration_ms` | integer | Wall-clock execution time in milliseconds. |

No dedicated fields for timeout or output-limit conditions — these are communicated through exit codes and stderr text, where agents actually read them. (D22)

### 2.3 Timeout Behavior

Default: **120 seconds**. Configurable at startup (`--timeout`). Overridable per-call (`timeout` parameter). (D25)

When a command exceeds its timeout:

1. The process is killed.
2. **No output is returned.** Both `stdout` and `stderr` are set to the error message only — partial output is discarded.
3. `stderr` is set to: `[kilntainers: command timed out after {N}s]`
4. `stdout` is set to empty string.
5. `exit_code` is set to **124** (matches the GNU `timeout` convention).
6. `exec_duration_ms` reflects the actual wall-clock time.

**Why no partial output:** Truncated output could break the LLM in unknown ways — it's data that is silently incomplete. Returning a clean error is predictable and forces the agent to adjust its approach (shorter command, incremental output, etc.).

### 2.4 Output Limit Behavior

Default: **2 MiB** (2,097,152 bytes), applied to the **combined** size of stdout and stderr. Configurable at startup (`--output-limit`). (D24)

When combined output exceeds the limit:

1. The process is killed.
2. **No output is returned.** Both `stdout` and `stderr` in the response are set to the error message only.
3. `stderr` is set to: `[kilntainers: output limit exceeded ({limit} bytes). Command terminated. No output returned. Re-run with head, tail, or grep to manage output size.]`
4. `stdout` is set to empty string.
5. `exit_code` is set to **1**.

**Why no partial output:** Returning truncated output means the agent is working with data that is silently incomplete. Returning an error forces the agent to re-run with explicit output management (`head -n 100`, `tail -n 50`, `grep pattern`, `wc -l`), which produces better results. (D24)

**Interaction with timeout:** If both conditions would trigger, whichever fires first takes effect. Both produce the same behavior (kill process, no output, error message in stderr), but with different stderr messages and exit codes (124 for timeout, 1 for output limit).

### 2.5 Error Categories

| Condition | MCP `isError` | Behavior |
|---|---|---|
| Command succeeds (exit 0) | `false` | Normal response |
| Command fails (non-zero exit) | `false` | Normal response with the command's exit code |
| Timeout | `false` | exit_code 124, no output, stderr notice only |
| Output limit exceeded | `false` | exit_code 1, no output, stderr notice only |
| Invalid parameters | `true` | MCP error message describing the validation failure |
| Sandbox dead | `true` | MCP error message; connection drops after response (D23) |

Commands that fail (non-zero exit code) are **not** MCP errors. They are successful tool calls that report the command's outcome. MCP `isError: true` is reserved for conditions where the tool itself cannot function or was called incorrectly.

### 2.6 Execution Model

- **Stateless:** Each call is independent. No shell session persists between calls. Working directory, shell variables, environment changes, and background processes do not carry over. To run multiple commands in context, chain them in one call (e.g., `cd /app && make test`).
- **Serial within a sandbox:** Exec calls to the same sandbox are queued and run one at a time. This is a v1 implementation detail, not an API contract — the API does not mention or enforce serialization. (D4, D29)
- **No stdin:** Stdin is not connected. Commands run non-interactively. Commands that attempt to read from stdin receive EOF immediately. Interactive tools (vim, less, etc.) will not work.
- **No `env` parameter:** Set environment variables inline: `FOO=bar some_command`. (D21)

---

## 3. Server Configuration

Kilntainers is configured through CLI parameters at startup. One server instance runs one backend with one configuration. Run multiple server instances for multiple configurations. (project_overview.md)

### 3.1 Core Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--backend` | string | `docker` | Backend to use. V1: `docker` only. |
| `--transport` | string | `stdio` | MCP transport: `stdio` or `http`. |
| `--host` | string | `127.0.0.1` | HTTP bind address (HTTP mode only). |
| `--port` | integer | `8080` | HTTP listen port (HTTP mode only). |
| `--network` | flag | disabled | Enable network access in sandboxes. (D5) |
| `--timeout` | integer (sec) | `120` | Default exec timeout. (D25) |
| `--output-limit` | integer (bytes) | `2097152` | Max combined stdout+stderr per exec. (D24) |
| `--extended-tool-instruction` | string | — | Appended to backend's tool description. (D16) |
| `--tool-instruction-override` | string | — | Replaces the entire tool description. (D16) |
| `--session-timeout` | integer (sec) | `300` | Idle session timeout (HTTP mode only). |

> **Note:** `--session-timeout` only applies to Streamable HTTP mode, where the server manages multiple concurrent sessions. In stdio mode, the session lives as long as the process runs. Passing session-timeout when stdio should error explaining why.

**Constraints:**

- `--extended-tool-instruction` and `--tool-instruction-override` are mutually exclusive. Providing both is a startup error. (D16)
- `--host`, `--port`, and `--session-timeout` error if passed to stdio mode where they do no apply.
- `--host` defaults to `127.0.0.1` (localhost only) for security. Set to `0.0.0.0` for remote access — see Section 9 security notes.

### 3.2 Docker Backend Parameters (V1)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--image` | string | `debian:bookworm-slim` | Docker image to use. Pulled inline if not present locally. (D11, D18) |
| `--shell` | string | `/bin/bash` | Shell binary used for `command` mode (e.g., `/bin/sh` for images without bash). |
| `--cpu` | string | *(no limit)* | Docker CPU limit (e.g., `"1.5"`). |
| `--memory` | string | *(no limit)* | Docker memory limit (e.g., `"512m"`). |
| `--docker-run-flag` | string | *(none)* | Additional flags passed to `docker run`. Repeatable. |

Backend parameters are flat CLI args, not namespaced. Only one backend runs per instance, so no collision risk. (D12). "--help" page should be organized/sorted by backend.

Resource limits (CPU, memory) default to no explicit limits — Docker defaults apply. For shared or production deployments, setting resource limits is recommended. (D26)

`--docker-run-flag` is a power-user escape hatch for any Docker option not covered by named parameters (e.g., `--docker-run-flag "--pids-limit=256"`). See Section 9 for security implications.

### 3.3 Startup Validation

On startup, before creating any sandbox or accepting connections:

1. **Parse and validate CLI arguments.** Reject unknown args, invalid types, and conflicting params (e.g., both override and extended instruction).
2. **Assemble tool description** (see Section 6). Fail if the result is empty.
3. **Backend validation.** The backend checks its prerequisites. For Docker:
   - Verify the Docker daemon is reachable (`docker info`).
   - Verify the configured shell exists in the image if feasible.
   - Report clear, actionable errors (e.g., `"Docker daemon is not running"` not `"connection refused"`).

All validation failures are reported to stderr and cause the process to exit with a non-zero code.

---

## 4. Connection Lifecycle

### 4.1 stdio Transport

One sandbox for the lifetime of the server process. (D8)

```
Process starts → validate config → start sandbox (pull image if needed)
  → accept MCP messages → ... → stdin closes or SIGTERM → stop sandbox → exit
```

Image pull happens during sandbox creation and blocks until complete. First run with a new image will be slow; subsequent runs use Docker's image cache. (D18)

### 4.2 Streamable HTTP Transport

Multiple concurrent sessions, each with its own independent sandbox. (D8, D28)

```
Server starts → validate config → listen on host:port

Per session:
  initialize request → start sandbox → return session ID
    → accept tool calls → ... → session ends → stop sandbox
```

Sessions are identified by the `Mcp-Session-Id` header per the MCP Streamable HTTP protocol. A session ends when:

- The client explicitly closes it.
- No requests are received for `--session-timeout` seconds (default: 5 minutes).
- The sandbox dies (D23).

Multiple sessions can be active simultaneously. Each has an independent sandbox — no shared state between sessions.

### 4.3 Sandbox Startup Sequence

When a new sandbox is needed (process start for stdio, new session for HTTP):

1. **Pull image** if not locally available — blocking. (D18) Progress should be logged to stderr so the user knows something is happening.
2. **Create and start** the sandbox (e.g., `docker run`).
3. **Verify readiness** — execute a trivial command (e.g., `echo kilntainers-ready`) to confirm the sandbox accepts exec calls.
4. **Accept tool calls.**

If any step fails, the connection is refused with a clear error message. For stdio, the process exits. For HTTP, the `initialize` response is an MCP error.

### 4.4 Graceful Shutdown

When a connection ends normally:

- **stdio** — stdin closes or process receives SIGTERM.
- **HTTP session** — client closes session, or idle timeout expires.
- **HTTP server** — process receives SIGTERM (all active sessions are torn down).

Shutdown sequence:

1. Any in-flight exec is **killed immediately.** The client is disconnecting — no one will receive the result.
2. The sandbox is stopped (e.g., `docker stop`).
3. Sandbox resources are cleaned up (e.g., container removed).
4. If cleanup takes more than **10 seconds**, force-kill and proceed.

### 4.5 Sandbox Death

If the sandbox dies unexpectedly (OOM, killed externally, Docker daemon crash): (D6, D23)

- **During an exec call:** Return an MCP error (`isError: true`) for the in-flight call with a message explaining the sandbox terminated unexpectedly, then drop the connection.
- **Between exec calls:** Drop the connection immediately. The client sees a disconnected server.

**stdio:** Process exits. Most MCP clients will offer to restart the server, which gives the user a fresh sandbox.

**HTTP:** The session is terminated. The client can create a new session and get a new sandbox.

No restart is attempted. Sandbox death is unrecoverable in v1. (D6)

---

## 5. Backend Interface Contract

This section defines what any backend must provide, expressed as external behavior. It does not prescribe Python class structure or method signatures — those belong in the architecture document.

### 5.1 Required Operations

Every backend must support these operations:

| Operation | Description |
|---|---|
| **Validate** | Given configuration (CLI args), check all prerequisites and parameter validity. Report actionable errors on failure. |
| **Start sandbox** | Create and start an isolated sandbox. Return an opaque handle for subsequent operations. Each call creates an independent sandbox. (D28) |
| **Stop sandbox** | Stop the sandbox and release all resources. Must be idempotent — safe to call on an already-stopped sandbox. |
| **Execute** | Run a command in a specific sandbox. Accepts either a `command` string or `args` array, plus `working_directory`, `timeout`, and `output_limit`. Returns `{stdout, stderr, exit_code, exec_duration_ms}`. |
| **Tool instructions** | Return a description string for the `shell_exec` tool, or null. If null, the server requires `--tool-instruction-override` or it fails to start. (D9, D16) |

### 5.2 Compatibility Contract

**Must be consistent across all backends:**

- Accept `command` (string) and execute it through an appropriate shell. The backend chooses the shell. (D20)
- Accept `args` (string array) and execute without shell interpretation. (D15)
- Respect `working_directory`, defaulting to the sandbox's native working directory. (D13)
- Enforce timeout: kill the process at expiration, return no output, set exit_code to 124, set stderr to the timeout notice.
- Enforce output limit: kill the process when combined stdout+stderr exceeds the limit, return no output, set stderr to the limit-exceeded message.
- Each exec call is independent — no persistent shell session, no state carried between calls.
- `stop` cleans up all sandbox resources completely.

**May vary across backends:**

- Shell type and capabilities (bash, POSIX sh, restricted shell, etc.) — must be documented in tool instructions.
- Available commands, tools, and packages.
- Filesystem layout, permissions, and available disk space.
- Resource limit options and how they're configured. (D26)
- Startup latency and exec performance.
- How networking is enabled or whether it's supported at all.

### 5.3 Backend Responsibilities

| Responsibility | Description |
|---|---|
| **Shell selection** | Choose the shell for `command` mode. Document it in tool instructions. (D20) |
| **Timeout enforcement** | Monitor execution time; kill the process when the timeout expires. |
| **Output limit enforcement** | Monitor combined stdout+stderr size; kill the process when the limit is exceeded. |
| **Lifecycle management** | Start and stop sandboxes cleanly and promptly. |
| **Death detection** | Detect when the sandbox dies (OOM, crash) and report to the MCP layer so the connection can be dropped. |
| **Resource isolation** | Ensure the sandbox cannot affect the host beyond configured permissions (network, mounts). |

The MCP layer passes configured values (timeout, output_limit, network flag) to the backend. The backend is responsible for enforcing them.

### 5.4 Orphaned Sandbox Cleanup

If the MCP server crashes (SIGKILL, power loss, panic), sandboxes may be left running without a controlling process.

**V1 strategy (Docker):**

- Containers are created with the `--rm` flag, so they are auto-removed when stopped.
- On normal shutdown, the server stops its containers (which triggers `--rm` auto-removal).
- Containers are labeled (`kilntainers=true`) for manual identification if needed (e.g., `docker ps --filter label=kilntainers=true`).

If the server crashes without stopping the container, `--rm` won't trigger (the container was never stopped). In v1, operators can manually find and remove orphaned containers using the label. Users can also use `docker stop` and `docker rm` directly.

**FUTURE:** A `kilntainers cleanup` CLI subcommand that discovers and removes orphaned sandboxes automatically. Not in v1 scope — `--rm` combined with manual cleanup via labels is sufficient.

### 5.5 Future: Mapped Working Directory Hooks

The backend interface will include an optional `mounts` parameter in the sandbox start operation. This parameter is **designed and documented** in v1 but **not implemented**. No backend needs to support it yet. (D14)

This ensures the interface won't need breaking changes when the mapped working directory feature is built. The design should accommodate Docker bind mounts, and document considerations for future backends (Modal volumes, E2B file sync, etc.).

---

## 6. Tool Description Assembly

The `shell_exec` tool's `description` field — the text the LLM sees — is assembled at startup from up to three sources. (D16)

**Assembly rules:**

1. **If `--tool-instruction-override` is provided:** Use it as the entire description. Backend instructions and `--extended-tool-instruction` are both ignored.
2. **Otherwise:** Start with the backend's tool instructions. If `--extended-tool-instruction` is provided, append it after a double newline (`\n\n`).
3. **If the backend returns null/empty and no override is provided:** Fail to start. Error message: `"Backend does not provide tool instructions. Supply --tool-instruction-override to configure the tool description."`
4. **If both `--tool-instruction-override` and `--extended-tool-instruction` are provided:** Fail to start. Error message: `"Cannot use both --tool-instruction-override and --extended-tool-instruction. Use override to replace the description entirely, or extended to append to the backend default."`

**What the tool description should convey:**

- What environment the agent is working in (OS, shell, available tools).
- The stateless execution model (no state between calls).
- Constraints (no stdin, output limits, default timeout).
- The actual configured values for timeout and output limit (so the description reflects the real environment, not hardcoded defaults).

---

## 7. Docker Backend: Default Tool Description

The Docker backend's `tool_instructions()` returns this text (with configured values substituted):

> Execute a shell command in an isolated Debian Linux sandbox. Commands run in bash. Each call is independent — no state (shell variables, working directory, background processes) persists between calls. Use the working_directory parameter or chain commands with && to control execution context.
>
> Stdin is not connected; commands run non-interactively. Commands time out after 120 seconds by default (override with the timeout parameter for long-running operations). Output is limited to 2 MB — commands exceeding this limit are terminated with no output returned. Use head, tail, or grep to manage large outputs.

The **120 seconds** and **2 MB** values in this text reflect the server's actual configured `--timeout` and `--output-limit`, not hardcoded defaults. If the user starts the server with `--timeout 300 --output-limit 10485760`, the description says "300 seconds" and "10 MB."

When a custom image is configured, the description may adjust (e.g., "an isolated Linux sandbox" instead of "an isolated Debian Linux sandbox") based on image metadata.

---

## 8. Example Configurations

These examples show how the same MCP interface adapts to different backends and configurations. Each shows the CLI invocation and the resulting tool description the LLM sees.

### 8.1 Docker — Zero Config

The simplest possible invocation. Everything uses defaults.

**CLI:**

```bash
kilntainers
```

**Effective configuration:** Docker backend, `debian:bookworm-slim` image, bash shell, network disabled, 120s timeout, 2 MiB output limit, stdio transport.

**Tool description seen by the LLM:**

> Execute a shell command in an isolated Debian Linux sandbox. Commands run in bash. Each call is independent — no state (shell variables, working directory, background processes) persists between calls. Use the working_directory parameter or chain commands with && to control execution context.
>
> Stdin is not connected; commands run non-interactively. Commands time out after 120 seconds by default (override with the timeout parameter for long-running operations). Output is limited to 2 MB — commands exceeding this limit are terminated with no output returned. Use head, tail, or grep to manage large outputs.

---

### 8.2 Docker — Python Data Science Image with Network

A custom image with pre-installed packages, network access for downloading data, and a longer timeout for heavy computation.

**CLI:**

```bash
kilntainers \
  --image python-datascience:latest \
  --network \
  --timeout 300 \
  --memory 4g \
  --extended-tool-instruction "This sandbox includes Python 3.12 with numpy, pandas, matplotlib, scipy, and scikit-learn pre-installed. Network access is enabled — you can pip install additional packages or download datasets with curl/wget."
```

**Tool description seen by the LLM:**

> Execute a shell command in an isolated Linux sandbox. Commands run in bash. Each call is independent — no state (shell variables, working directory, background processes) persists between calls. Use the working_directory parameter or chain commands with && to control execution context.
>
> Stdin is not connected; commands run non-interactively. Commands time out after 300 seconds by default (override with the timeout parameter for long-running operations). Output is limited to 2 MB — commands exceeding this limit are terminated with no output returned. Use head, tail, or grep to manage large outputs.
>
> This sandbox includes Python 3.12 with numpy, pandas, matplotlib, scipy, and scikit-learn pre-installed. Network access is enabled — you can pip install additional packages or download datasets with curl/wget.

*Note how `--extended-tool-instruction` is appended after a double newline. The backend's base description reflects the custom timeout (300s). The extended instruction adds context about the custom image's capabilities.*

---

### 8.3 Modal Remote VM (Hypothetical Future Backend)

A cloud VM backend for heavy computation with GPU access. Shows how backend-specific params (like `--gpu`) coexist with core params.

**CLI:**

```bash
kilntainers \
  --backend modal \
  --image python:3.12-slim \
  --network \
  --timeout 600 \
  --gpu a10g \
  --extended-tool-instruction "This is a remote cloud VM with an NVIDIA A10G GPU. CUDA toolkit and PyTorch are available. Network access is enabled."
```

**Tool description seen by the LLM:**

> Execute a shell command in a remote cloud VM (Modal). Commands run in bash. Each call is independent — no state persists between calls. Use the working_directory parameter or chain commands with && to control execution context.
>
> Stdin is not connected; commands run non-interactively. Commands time out after 600 seconds by default (override with the timeout parameter for long-running operations). Output is limited to 2 MB — commands exceeding this limit are terminated with no output returned. Use head, tail, or grep to manage large outputs.
>
> This is a remote cloud VM with an NVIDIA A10G GPU. CUDA toolkit and PyTorch are available. Network access is enabled.

*Note: `--gpu` and authentication are hypothetical examples of backend-specific flat args (D12). The MCP tool interface (`shell_exec` with the same parameters and response schema) is identical across backends. Only the backend's tool description changes to reflect the environment.*

---

### 8.4 WASI BusyBox Sandbox (Hypothetical Future Backend)

A minimal WebAssembly sandbox with limited capabilities. Uses `--tool-instruction-override` because the environment is so different from a full Linux system that the entire description must be custom-written.

**CLI:**

```bash
kilntainers \
  --backend wasi-busybox \
  --tool-instruction-override "Execute a command in a lightweight POSIX sandbox. Only sh (POSIX shell) is available — do not use bash-specific syntax like arrays, [[ ]], or process substitution. Available commands: ls, cat, grep, sed, awk, sort, uniq, wc, head, tail, find, mkdir, rm, cp, mv, echo, printf, test, tr, cut, tee, xargs, dirname, basename. No package manager. No network access. No persistent state between calls. Stdin is not connected. Commands time out after 120 seconds. Output is limited to 2 MB."
```

**Tool description seen by the LLM:**

> Execute a command in a lightweight POSIX sandbox. Only sh (POSIX shell) is available — do not use bash-specific syntax like arrays, [[ ]], or process substitution. Available commands: ls, cat, grep, sed, awk, sort, uniq, wc, head, tail, find, mkdir, rm, cp, mv, echo, printf, test, tr, cut, tee, xargs, dirname, basename. No package manager. No network access. No persistent state between calls. Stdin is not connected. Commands time out after 120 seconds. Output is limited to 2 MB.

*Note: This uses `--tool-instruction-override` because the WASI backend's `tool_instructions()` may return null (the backend can't describe itself adequately), or the user wants full control over describing the limited environment. The override replaces everything — the user is responsible for the complete description.*

---

## 9. Security Model

### 9.1 Core Security Properties

| Property | Mechanism |
|---|---|
| **Process isolation** | The agent calls into the sandbox; it does not run inside it. No agent credentials need to exist in the sandbox. |
| **Ephemeral** | Sandboxes are destroyed on disconnect. No persistent state to compromise. |
| **Network isolation** | Network disabled by default. Prevents exfiltration of any data the agent writes into the sandbox. Opt-in via `--network`. (D5) |
| **Host isolation** | No host filesystem mounts by default. The sandbox cannot access host files or resources. |

### 9.2 Threat Model

| Threat | Mitigation |
|---|---|
| **Data exfiltration** — agent writes a secret into the sandbox, then sends it over the network | Network disabled by default. When enabled, the operator accepts this risk. |
| **Resource exhaustion** — CPU abuse, memory bombs, disk fill, fork bombs | Backend-specific resource limits (`--cpu`, `--memory`, Docker PID limits via `--docker-run-flag`). Exec timeout prevents indefinite CPU use. |
| **Container escape** | Relies on the backend's isolation technology (Docker, WASI). Not a Kilntainers-specific concern — use up-to-date container runtimes. |
| **Host filesystem access** | No mounts by default. Future mapped working directory will be scoped to a single user-specified directory. (D14) |
| **MCP server abuse** (HTTP mode) | Default bind to `127.0.0.1`. `--session-timeout` reclaims idle resources. No built-in authentication — production HTTP deployments should use a reverse proxy with auth. |

### 9.3 Operator Responsibilities

- **Set resource limits** for shared or production deployments (`--cpu`, `--memory`, PID limits).
- **Use `--network` only when required.** The default (disabled) is the safe choice.
- **Review `--docker-run-flag` values.** This escape hatch can weaken isolation (e.g., `--privileged`, host mounts). Use with care.
- **Do not expose HTTP transport to untrusted networks** without authentication. The default `--host 127.0.0.1` binding restricts to localhost.
- **Use custom images with minimal software** when security is a concern (smaller attack surface).

---

## 10. Decisions Made in This Spec

The following open items from [spec_queue.md](spec_queue.md) were resolved in this spec. These should be added to [decisions.md](decisions.md) as new decision entries.

| Item | Resolution | Spec Section |
|---|---|---|
| **Stdin policy** | Not supported. Stdin not connected; commands receive EOF. Documented in tool description and execution model. | §2.6 |
| **Error contract** | Normal results (including timeout and output limit) use `isError: false` with no output returned. Infrastructure failures (sandbox death, invalid params) use `isError: true`. Exit code 124 for timeout, 1 for output limit. Messages in stderr. | §2.5 |
| **Output limit scope** | Combined stdout + stderr, not per-stream. | §2.4 |
| **Graceful shutdown** | In-flight exec killed immediately on disconnect. Sandbox torn down with 10s force-kill timeout. | §4.4 |
| **Backend abstraction design** | Five required operations: validate, start, stop, exec, tool_instructions. Behavioral contract, not method signatures. | §5.1 |
| **Backend responsibilities** | Timeout enforcement, output limit enforcement, shell selection, lifecycle management, death detection, resource isolation. | §5.3 |
| **Backend abstraction boundaries** | Compatibility contract defines what must be consistent (command/args, working dir, timeout, output limit, stateless exec, cleanup) and what may vary (shell, commands, resources, performance). | §5.2 |
| **Orphaned sandbox cleanup** | V1: Docker `--rm` flag + `kilntainers=true` label for manual identification. `kilntainers cleanup` subcommand deferred to FUTURE. | §5.4 |
| **Container naming/identification** | Docker containers labeled `kilntainers=true` for discovery. | §5.4 |
| **Resource defaults (Docker)** | No explicit limits by default (Docker defaults). Operators set limits for production. | §3.2 |
| **Container startup flow** | Pull → create/start → verify readiness → accept calls. Pull failure = startup error. | §4.3 |
| **Docker config approach** | Flat CLI args for v1 with `--docker-run-flag` escape hatch for uncovered options. | §3.2 |
| **Tool description text** | Drafted for Docker backend with dynamic timeout and output limit values. | §7 |
| **Startup parameters** | Full schema in §3 including transport, host, port, session-timeout. | §3.1, §3.2 |
| **Connection lifecycle** | stdio: one sandbox per process. Streamable HTTP: one sandbox per session, identified by Mcp-Session-Id. 5-minute idle timeout (configurable). | §4 |
| **Security model** | Threat model covering exfiltration, resource abuse, container escape, host access, and HTTP exposure. | §9 |
| **D8 transport correction** | Streamable HTTP, not SSE. These are different transports; SSE is deprecated. D8 updated. | §1 |

**Deferred:**

- **Testing strategy** — implementation and CI concern, not functional spec scope. Remains open in spec_queue.md.
