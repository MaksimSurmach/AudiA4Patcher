# Codex handoff: Audi A4 Patcher

## Existing deliverable

This folder is a uv-managed Python project. Read README.md and AGENTS.md. It has a
CLI/TUI, BLE ELM327 CAF1 transport, strict incoming ISO-TP reassembly, explicit
VAG reads, provenance-aware dictionary, durable snapshots, guarded 2E writes,
single-record restores, reconciliation and a deterministic persistent simulator.
The 0.2 interface uses Textual with device discovery/selection, searchable
snapshots and records, a dictionary browser, and keyboard navigation. Read
docs/TUI_GUIDE.md for the actual workflow. The import package is still
`audi_workbench`; `audi-a4-patcher` and `audi-workbench` call the same CLI.

Software checks do not establish hardware support. Keep verification evidence
explicit about the version and platform.

The user owns a Mac and iPhone, an Audi A4 B9 2017 with MHS2 5F, and a Vgate iCar
Pro 2S. Do not substitute MQB feature maps for MLB evo or recommend Windows as a
requirement. Previous observed BLE advertisement: IOS-Vlink, service 18F0.
Known candidate GATT: notify 2AF0/write 2AF1; confirm current discovery.

## First task: analyze a real collect export

Use the user's support ZIP as evidence. Do not infer successful hardware
operation from simulator tests. Do not write to the vehicle as part of capture.

1. Check GATT discovery, ATI and required AT command acceptance.
2. Check 5F TX773/RX7DD, 19 TX710/RX77A, 01 TX7E0/RX7E8 actual responses.
3. Validate full frame sequences and the DID/SID echoes. Diagnose no-data,
   wrong headers, adapter limitations or unsupported DID independently.
4. Report the exact real 5F part number, hardware, software and ASAM. Preserve raw
   padded identities; do not replace them with friendly normalized strings.
5. Inspect 0600 and 22AD only as raw values until an exact definition is found.
   Current 22AD comes from an earlier probe; its semantic layout is unverified.
6. Produce a candidate JSON dictionary with citations, exact fingerprint,
   record lengths, fields and confidence. Keep unverified definitions read-only.

## Concrete transport gap: outgoing long ISO-TP

BLELink currently rejects requests longer than seven UDS bytes. A Write DID
request therefore permits at most four value bytes. There is no hidden override.
Incoming long responses already work. `ATAL` is not an outgoing segmenter.

If the next task requires long-coding writes, implement a separate sender with
an explicit capability flag. Do not increase max_request_bytes without the
protocol implementation. Required engineering evidence:

- CAF0 raw CAN frame formatting and deliberate restoration of adapter state.
- FF -> ECU FC handling: CTS, WAIT with bounded wait, overflow, block size.
- CF sequencing, modulo-16 wrap, block boundaries, STmin values and reserved
  encodings; fail closed for unsupported timing. BLE timing must be measured.
- Responses-disabled intermediate transmissions and enabled final response,
  if using ELM R0/R1, with tests for every error/restore boundary.
- Pending responses, final acknowledgement, transport failures and cancellation.
- One outstanding operation; no background tester-present that interleaves frames.
- Fake-ELM transcript tests plus actual read-only/bench evidence. Simulator
  success alone cannot mark the capability hardware-validated.
- Preserve one-shot writes and durable unknown-state recovery. No retries of 2E.

Explain any adapter limitation precisely. Avoid implying that the existing
Vgate is guaranteed to support a long write or to recover firmware failure.

## Further gaps, in dependency order

1. Exact MHS2 definitions for coding/adaptations, especially Apple_DIO, with
   independent source evidence matching the real SW/ASAM.
2. Only if required by those definitions: the exact authorized diagnostic
   session/access procedure. Do not send a decimal VCDS login number as a raw
   UDS key or guess seed/key algorithms.
3. Verify a benign reversible short record on the stationary vehicle after
   inspecting the saved plan and backup, with normal app confirmation.
4. Check persistence after an ignition cycle by reading again.
5. Extend read-only ECU profiles as needed using documented physical addresses.

CarPlay license/activation, compatible USB hardware and coding state are separate
questions. The current app does not patch firmware, FECs or Component Protection.

## Verification commands

```bash
uv sync --locked --dev
uv run --locked pytest -q
uv run --locked ruff check audi_workbench tests
uv run --locked audi-a4-patcher --data-dir ./demo-check --demo collect
uv run --locked audi-a4-patcher --data-dir ./demo-check --demo
uv build
```

The default command opens the TUI; `tui` is an equivalent explicit subcommand.
Maintain `pyproject.toml` and regenerate `uv.lock` together when dependencies
change. Do not hand-edit the lock. The development Python pin is 3.12; the
package supports Python 3.11+. CI has Linux and macOS jobs using synthetic tests.

Add tests for behavior at the relevant boundary. In particular, preserve tests
for failed backup, stale plan, identity mismatch, invalid masks, concurrency,
timeout after acceptance and readback denial. Never weaken them to make a new
transport implementation pass.

## Repository

The user selected https://github.com/MaksimSurmach/AudiA4Patcher as the project
repository. Keep source, dictionaries, tests and documentation together there.
Real captures and support exports are user evidence and must not enter public
commits. The sample snapshot and demo recipes are explicitly synthetic.

The original archive was version 0.1. The uv workflow and navigable English UI
are version 0.2. Use the current repository commands above when continuing work.
