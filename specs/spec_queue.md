# Kilntainers - Spec Queue

Checklist of items that need to be fully specified in the functional spec. These have been identified during planning Q&A but intentionally deferred from early decision-making to get proper treatment in the spec.

Items marked with [x] have been decided (see [decisions.md](decisions.md)) but may still need detailed spec treatment. Items marked [ ] are still open.

---

## Exec Behavior

- [x] **Command interface format**: Both string (`command`) and array (`args`), mutually exclusive. Backend receives whichever was provided and decides how to invoke (e.g., which shell for `command` mode). (Decision D15 revised, D20)
- [x] **Exec parameters**: `command` or `args`, `working_directory` (optional), `timeout` (optional, seconds). No `env` param. (Decision D21)
- [x] **Exec timeout policy**: 120s default, configurable at startup (`--timeout`), per-call override. Communicated via exit code + stderr. (Decision D25)
- [x] **Output size limits**: 2MB default, configurable at startup (`--output-limit`). Exceeding limit = error (kill process, return error, no partial output). Agent retries with head/tail/grep. (Decision D24)
- [x] **Binary output handling**: No special handling. UTF-8 mangling is fine, 2MB limit protects. (Decision D27)
- [ ] **Stdin policy**: Explicitly document that stdin is not supported (commands run non-interactively). Include in tool description.
- [x] **Environment variables**: No `env` param. Use inline `FOO=bar cmd` syntax. (Decision D21)
- [x] **Working directory default**: Container's WORKDIR, falling back to `/`. (Decision D13)
- [x] **Exec response schema**: `{stdout, stderr, exit_code, exec_duration_ms}`. No custom fields for timeout/truncation -- these are communicated through output text. (Decision D22)

## Error Model

- [x] **Sandbox death handling**: Drop the MCP connection. In-flight exec gets an error response first, then connection drops. (Decision D23)
- [ ] **Error contract for exec failures**: Enumerate what exit codes and stderr messages are used for infrastructure failures (timeout, truncation, etc.) vs normal command failures.
- [ ] **Graceful shutdown behavior**: What happens on disconnect? Timeout for container teardown?

## Backend Interface

- [ ] **Backend abstraction design**: ABC (Decision D19). Exact method signatures for init, start, stop, exec, tool_instructions still need spec. Exec receives either `command` (str) or `args` (list[str]) -- backend handles shell wrapping for command mode. (Decision D20)
- [ ] **Backend responsibilities**: What must every backend handle? (Cleanup on stop, timeout enforcement, output capture, shell selection for command mode)
- [ ] **Orphaned sandbox cleanup**: Strategy for cleaning up sandboxes left behind after MCP server crash. Naming conventions, Docker `--rm` flag, cleanup CLI command, garbage collection.
- [x] **Backend parameter passing**: Flat CLI args, no namespacing. (Decision D12)
- [ ] **Backend abstraction boundaries**: What can leak? What must be consistent? Define a "compatibility contract" that all backends must satisfy.
- [x] **Backend-provided tool instructions**: Override replaces everything; extended appends to backend with double newline; both provided = error; backend null + no override = fail to start. (Decision D16)
- [x] **Shell selection**: Backend responsibility, not MCP layer. Backend's tool description must indicate what shell/command syntax is supported. (Decision D20)

## Docker Backend (V1)

- [x] **Default base image**: Debian slim (bookworm-slim or current stable). (Decision D11)
- [ ] **Container naming convention**: For identification and cleanup.
- [ ] **Resource defaults**: Default CPU, memory, disk limits? Or unlimited by default?
- [x] **Docker SDK vs CLI**: CLI via subprocess. (Decision D10)
- [ ] **Container startup flow**: Image pull is inline/blocking (Decision D18). Still need to spec: startup health check, readiness detection, error handling on pull failure.
- [ ] **Docker config complexity**: Revisit flat CLI args vs config file for Docker specifically. Many potential params (image, shell path, tool description strings, resource limits, mounts). A `--config` flag pointing to a JSON/YAML file may be needed. (TODO from Q&A round 3)

## MCP Interface

- [x] **Transport**: Both stdio and HTTP/SSE streaming. (Decision D8)
- [x] **Tool name**: `shell_exec`. No global default description -- backend provides or user overrides. (Decision D9)
- [ ] **Tool description text**: Exact wording for the Docker backend's default tool description. Must indicate bash support, ephemeral nature, no state between calls, etc.
- [ ] **MCP server startup parameters**: Full CLI parameter schema (backend, network_enabled, extended_tool_instruction, tool_instruction_override, timeout, output_limit, and backend-specific flat args).
- [ ] **Connection lifecycle details**: Exactly what happens on connect (start sandbox, wait for ready, then accept tool calls?) and disconnect (stop sandbox, cleanup, timeout for graceful shutdown?). On sandbox death: drop connection (Decision D23).

## Security

- [x] **Resource limits as part of interface**: Purely backend-specific config, not part of the ABC. (Decision D26)
- [ ] **Security model documentation**: Threat model beyond network exfiltration (CPU abuse, disk fill, fork bombs, container escape).

## Testing

- [ ] **Testing strategy**: Unit test approach, integration test approach (real Docker), CI/CD requirements (Docker-in-Docker or alternatives), mock backend for testing MCP layer independently.

## Future-Proofing

- [x] **Mapped working directory hooks**: Design optional `mounts`/`volumes` parameter in backend interface spec. Document but don't implement in v1. (Decision D14)
- [x] **Parallel exec interface**: API says nothing about serialization. Internal v1 implementation queues and serializes. No API change needed for future parallelism. (Decision D29)
- [x] **Multi-sandbox support**: Required in v1 (HTTP/SSE has concurrent connections). No global sandbox state. Each sandbox is an independent object with explicit handle. (Decision D28)

---

*Last updated after planning Q&A round 3. Items will be checked off as they are addressed in the functional spec.*
