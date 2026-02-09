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
- [x] **Stdin policy**: Not supported. Stdin not connected; commands receive EOF. Documented in tool description and execution model. (Functional spec §2.6)
- [x] **Environment variables**: No `env` param. Use inline `FOO=bar cmd` syntax. (Decision D21)
- [x] **Working directory default**: Container's WORKDIR, falling back to `/`. (Decision D13)
- [x] **Exec response schema**: `{stdout, stderr, exit_code, exec_duration_ms}`. No custom fields for timeout/truncation -- these are communicated through output text. (Decision D22)

## Error Model

- [x] **Sandbox death handling**: Drop the MCP connection. In-flight exec gets an error response first, then connection drops. (Decision D23)
- [x] **Error contract for exec failures**: Normal results (including timeout/output limit) use `isError: false`. Infrastructure failures use `isError: true`. Exit code 124 for timeout, 1 for output limit. Messages in stderr. Output limit scope: combined stdout+stderr. (Functional spec §2.5)
- [x] **Graceful shutdown behavior**: In-flight exec killed immediately on disconnect. Sandbox torn down with 10s force-kill timeout. (Functional spec §4.4)

## Backend Interface

- [x] **Backend abstraction design**: ABC (Decision D19). Five required operations: validate, start, stop, exec, tool_instructions. Behavioral contract defined. (Functional spec §5.1)
- [x] **Backend responsibilities**: Timeout enforcement, output limit enforcement, shell selection, lifecycle management, death detection, resource isolation. (Functional spec §5.3)
- [x] **Orphaned sandbox cleanup**: `kilntainers cleanup` subcommand. Docker: `--rm` flag + `kilntainers=true` labels + cleanup command. (Functional spec §5.4)
- [x] **Backend parameter passing**: Flat CLI args, no namespacing. (Decision D12)
- [x] **Backend abstraction boundaries**: Compatibility contract defines what must be consistent and what may vary. (Functional spec §5.2)
- [x] **Backend-provided tool instructions**: Override replaces everything; extended appends to backend with double newline; both provided = error; backend null + no override = fail to start. (Decision D16)
- [x] **Shell selection**: Backend responsibility, not MCP layer. Backend's tool description must indicate what shell/command syntax is supported. (Decision D20)

## Docker Backend (V1)

- [x] **Default base image**: Debian slim (bookworm-slim or current stable). (Decision D11)
- [x] **Container naming convention**: Docker containers labeled `kilntainers=true` for identification by cleanup command. (Functional spec §5.4)
- [x] **Resource defaults**: No explicit limits by default (Docker defaults). Operators set limits for production. (Functional spec §3.2)
- [x] **Docker SDK vs CLI**: CLI via subprocess. (Decision D10)
- [x] **Container startup flow**: Pull → create/start → verify readiness (trivial exec) → accept calls. Pull failure = startup error. (Functional spec §4.3)
- [x] **Docker config complexity**: Flat CLI args for v1 with `--docker-run-flag` escape hatch for uncovered Docker options. (Functional spec §3.2)

## MCP Interface

- [x] **Transport**: Both stdio and Streamable HTTP (updated terminology from D8's "HTTP/SSE"). (Decision D8, Functional spec §1)
- [x] **Tool name**: `shell_exec`. No global default description -- backend provides or user overrides. (Decision D9)
- [x] **Tool description text**: Drafted for Docker backend with dynamic timeout/output-limit values. (Functional spec §7)
- [x] **MCP server startup parameters**: Full CLI parameter schema including core params and Docker backend params. (Functional spec §3.1, §3.2)
- [x] **Connection lifecycle details**: stdio: one sandbox per process. HTTP: one sandbox per session (Mcp-Session-Id), 30min idle timeout. Startup: pull → start → verify → accept. Shutdown: kill in-flight, stop sandbox, 10s force-kill. Death: drop connection. (Functional spec §4)

## Security

- [x] **Resource limits as part of interface**: Purely backend-specific config, not part of the ABC. (Decision D26)
- [x] **Security model documentation**: Threat model covering exfiltration, resource abuse, container escape, host filesystem access, and HTTP exposure. Operator responsibilities documented. (Functional spec §9)

## Testing

- [ ] **Testing strategy**: Unit test approach, integration test approach (real Docker), CI/CD requirements (Docker-in-Docker or alternatives), mock backend for testing MCP layer independently.

## Future-Proofing

- [x] **Mapped working directory hooks**: Design optional `mounts`/`volumes` parameter in backend interface spec. Document but don't implement in v1. (Decision D14)
- [x] **Parallel exec interface**: API says nothing about serialization. Internal v1 implementation queues and serializes. No API change needed for future parallelism. (Decision D29)
- [x] **Multi-sandbox support**: Required in v1 (HTTP/SSE has concurrent connections). No global sandbox state. Each sandbox is an independent object with explicit handle. (Decision D28)

---

*Last updated after functional spec. All items resolved except Testing Strategy, which is deferred to the architecture/implementation phase.*
