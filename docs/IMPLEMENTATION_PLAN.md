# Initial implementation plan (historical v0.1)

This is the original planning record. The current uv workflow, default Bleak and
Textual dependencies, repository and v0.2 interface are documented in
[README.md](../README.md) and [CODEX_HANDOFF.md](CODEX_HANDOFF.md).
The outgoing long ISO-TP path proposed below was not implemented; requests
longer than seven UDS bytes remain blocked.

## Goal
Runnable local diagnostic workbench, with Mac BLE transport, a terminal UI and
CLI, reproducible read-only capture, extensible evidence-backed decoding, and
guarded single-record writes/restores. Hardware availability is external; real
vehicle functionality must be reported as unverified until a first live capture.

## Decisions
- Python 3.11+, standard-library core, optional Bleak and Textual.
- 5F as default; 01 and 19 for identification / vehicle conditions. Explicit
  profile selection; CAN addresses come from cited primary source projects.
- No firmware flashing / license activation / arbitrary security unlock.
- Real writes require exact ECU identity, explicit locally verified dictionary
  entry and explicit confirmation. Demo recipes are impossible to use on BLE.
- Read current value, enforce same length / allowed mask, durably back up,
  re-check stale state, write once, verify readback. On ambiguity journal UNKNOWN,
  retain backup, no automatic write retry or rollback.
- Long requests require an explicitly enabled experimental manual ISO-TP path;
  ordinary ELM CAF1 requests are limited to seven UDS bytes.

## Units
1. Transport: BLE device selection/GATT discovery, ELM initialization, strict
   response framing, UDS over 11-bit CAN, command journal, capability limits.
2. Data: JSON dictionary with provenance and exact identity matching, bit/enum
   decoding, immutable snapshots/diffs, persistent deterministic simulator.
3. Workbench: read capture, plan, preflight, backups, write and restore journals,
   process lock, bounded harvest and export for further dictionary research.
4. CLI/TUI/docs: runnable demo, Mac first-run guide, controls for all workbench
   operations, evidence report and Codex handoff.

## Verification
Behavioral tests: fragmented BLE replies, malformed/truncated ISO-TP, flow
control, wrong ECU/SID/DID, transport timeouts; invalid/mismatched dictionary;
stale state, VIN/software mismatch, low voltage, moving/running engine, length
or mask violation, confirmation mismatch, backup failure, ambiguous writes,
concurrent access, restore from verified transaction. Demo end-to-end capture,
decode, edit bit, apply, capture/diff, restore; read-only capture emits no write.
Run TUI headlessly. Inspect final code with independent reviewers.

## Definition of done
Package + documentation + tests + sample capture, demo end-to-end passes,
independent review findings addressed, actual hardware limitations named,
archive saved for download. No external repo is required for this delivery.
