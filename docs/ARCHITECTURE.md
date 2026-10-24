# Audi A4 Patcher architecture

## Runtime and dependencies

The project is managed by uv. `pyproject.toml` declares Bleak and Textual as
default runtime dependencies, plus a separate developer dependency group.
`uv.lock` records resolved cross-platform dependencies. `.python-version` pins
the development interpreter to 3.12; supported Python versions start at 3.11.

`audi-a4-patcher` invokes `audi_workbench.cli:main`. With no subcommand, the CLI opens the terminal UI.
Explicit CLI subcommands and `python -m audi_workbench` remain available.

## Layers

| File | Responsibility |
|---|---|
| `model.py` | ECU profile, raw identity identifiers, protocol and error contracts |
| `transport.py` | BLE selection/GATT, prompt channel, ELM configuration, incoming ISO-TP |
| `simulator.py` | Persistent synthetic ECU using the same diagnostic interface |
| `dictionary.py` | Validated declarative definitions, field decoding and writable masks |
| `store.py` | Atomic artifacts, integrity hashes, process lock, durable traces, local export |
| `service.py` | Capture, edit plans, preflight, one-shot write/readback, restore and reconciliation |
| `cli.py` / `tui.py` | User controls over the same service; no alternative write path; `cli.py` also drafts read-only dictionary candidates |

The application has no server, cloud API, telemetry or background vehicle polling.
The TUI uses one asyncio event loop. Diagnostic operations are serialized.
External dictionaries contain data only; no Python expressions or commands run.

## Terminal workspace

Textual provides tab navigation, keyboard bindings, tables, filtering and the
worker lifecycle. The six sections are Devices, Capture, Explore, Changes,
Dictionary and Evidence. UI actions use the same Workbench service as the CLI.

Discovery lists advertised BLE identities without opening a diagnostic session.
Selecting a discovered row uses its exact address/UUID; a manual unique name is
also supported. A selected source is not a connected-status claim. Each online
operation enters a Workbench session and closes it on exit. Simulator and
Offline remain explicit alternative sources.

The busy flag is set before dispatching an operation. Conflicting controls and
source changes are disabled until it completes, duplicate dispatch is rejected,
and guarded quit actions wait for the result. Changing the source or loading a
plan clears previous write consent. Searching and selecting raw data never
grants permission to write it.

User interaction details are in [TUI_GUIDE.md](TUI_GUIDE.md). No UI control is
an alternative route around the service's preflight and transaction journal.

## Capture

Each requested DID gets its own positive value or explicit error. A received
negative response or a synchronized ELM `NO DATA` does not manufacture an empty
successful value. Unsupported individual records do not invalidate other reads.
Broken framing, a lost prompt, or a disconnected link stops the collection.
The partially completed snapshot is checkpointed after each attempted DID.

`complete` means collection reached the end of its requested set and obtained
at least one successful record; it does not mean every DID is supported. Inspect
`records[did].ok` and `requested_dids` for exact coverage. Initial identification
does not prove that every ECU profile in the app is installed on the vehicle.

## Write transaction

Plan preview is offline. The online engine validates the plan and its current
dictionary entry, then reads vehicle conditions, current identity and old value.
It saves a fresh snapshot, verifies that backup, saves a `prepared` transaction,
checks any required session and conditions, and reads the old value again.

The engine durably records `attempting` **before** sending the one `2E` request.
It then checks the response and performs a separate `22` readback.

| Status | Meaning | Next useful action |
|---|---|---|
| `prepared` / `not_sent` | No write call was started | Inspect preflight error |
| `attempting` | Durable intent exists; process may have stopped mid-operation | Reconnect and reconcile |
| `verified` | Positive write response and matching immediate readback | Keep receipt; later capture can check persistence |
| `rejected` | A final negative write response was received | Inspect NRC; do not guess access keys |
| `unknown` | Acceptance or readback is uncertain | Reconnect and reconcile |
| `verification_failed` | ECU acknowledged write but returned different data | Inspect and reconcile; no automatic rollback |
| `reconciled_before` | Fresh read equals the pre-image | Make a fresh plan if needed |
| `reconciled_after` | Fresh read equals the intended post-image | Restore preview can be built |
| `reconciled_other` | Fresh read differs from both | Inspect actual bytes and create a reviewed plan |

Restore is another guarded transaction, for the single previously changed
record. It cannot recover an unreachable controller or replace its firmware.
An uncertain write is never silently retried or rolled back.

## First hardware validation

Owner: the user running the app on the Mac connected to the vehicle.
Window: first `collect`, followed by inspection of its exported evidence.

Expected signals: the intended BLE identity and UART properties, required AT
commands returning OK, physical RX IDs matching their requests, complete ISO-TP
messages, raw ECU identity and per-DID success/error coverage. No request with
service `2E` should appear in a capture-only trace. No hardware write capability
is inferred from successful reads.

Stop the session on poisoned command-stream state, malformed ISO-TP, unknown
GATT profile, or an unsupported required command. The mitigation is closing the
link and analyzing the trace. Reflashing, repeated writes and automated ECU
resets are not recovery steps implemented by this program.

There is no deployment monitor to configure: this is a local user-launched app.
Write operation failure is observable in durable transaction status, the TUI
log and CLI exit status. Codes: 0 success, 2 expected error, 3 unverified write,
4 incomplete capture/collection, 130 interrupted.
