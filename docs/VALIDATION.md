# Release validation: 0.1.0

Validation date: 2026-10-24.

**Result:** the complete automated suite passed: **124 tests and 42 subtests**.
Ruff, Python compilation and whitespace checks passed. A separate simulator CLI
collection completed for 5F, 19 and 01 and produced a support ZIP. These results
establish software behavior in the tested environment, not vehicle compatibility.

## Environment

| Component | Tested version |
|---|---|
| OS / architecture | Linux x86_64 |
| Python | 3.12.14 |
| Bleak | 2.1.1 |
| Textual | 7.5.0 |
| pytest | 9.1.1 |
| pytest-asyncio | 1.4.0 |
| Ruff | 0.17.2 |

The application targets Python 3.11+ on macOS. This release was not executed
on a physical Mac, iPhone, Bluetooth adapter or vehicle. The iPhone is not a
runtime target for this Python TUI. Platform-specific BLE dependencies are
resolved on installation; `constraints-tested.txt` pins the direct versions
above without imposing a Linux dependency freeze on macOS.

## Commands and observed results

```text
.venv/bin/python -m pytest -q
124 passed, 42 subtests passed in 11.50s

.venv/bin/python -m ruff check audi_workbench tests
All checks passed!

.venv/bin/python -m compileall -q audi_workbench
exit 0

git diff --check
exit 0

.venv/bin/python -m audi_workbench --data-dir /tmp/audi-workbench-final-demo --demo collect
exit 0; complete=true; three completed captures; local support ZIP created
```

For a fresh repeat, choose a new data directory; the simulator intentionally
preserves state between sessions. Install development dependencies first:

```bash
python -m pip install -e '.[all,dev]' -c constraints-tested.txt
python -m pytest -q
python -m ruff check audi_workbench tests
audi-workbench --data-dir ./demo-validation --demo collect
```

## Maintained behavioral coverage

| Area | Evidence exercised |
|---|---|
| ELM and incoming ISO-TP | Prompt assembly, frame addressing, lengths, padding, consecutive sequence, pending responses, mismatched SID/DID, adapter errors, fragmented notifications, poisoned streams and request-size rejection |
| BLE lifecycle | GATT selection, adapter setup, connection invalidation, clean NO DATA continuation, mixed NO DATA rejection, bounded cleanup, preserved cancellation/error, unknown receipt and lock release |
| Dictionary | Structure, exact identity matching, masks, length, enum validation before and after a change, unknown decoding, demo/live isolation and dictionary changes after planning |
| Persistence and selection | Hash integrity, process lock, atomic artifacts, backup and journal failure boundaries, chronological Latest selection despite reversed UUID names, and deterministic ties |
| Write/restore service | Fresh value and identity checks, voltage/speed/RPM guards, one write, readback, reverse plan, stale-plan rejection, unresolved attempts, reconnect and read-only reconciliation |
| Session changes | Correct extended-session confirmation, rejection, malformed/wrong-session replies, and changed value or vehicle conditions during the switch |
| Cancellation and failures | Event-held cancellation during write response and readback, final-receipt save failure, no automatic retry, durable unknown/attempting status and recovery without another write |
| CLI | Subprocess help, explicit live-device requirement, capture, inspect, plan, apply, restore, three-ECU collection, export, explicit DID lists and read-only dictionary templates |
| TUI | Headless actual buttons and widgets for capture/change/restore, failed inputs, Latest selection, active quit guards, duplicate action suppression, disabled controls, successful completion and failure reset |

`docs/tui-preview.png` is a rendered screenshot from the actual Textual app
with synthetic simulator data. It is not a concept mockup or vehicle capture.
`examples/demo-snapshot.json` is also explicitly synthetic.

The release archive contains the tracked source, package data, tests, examples
and documentation, excluding virtual environments, caches and real user logs.
Archive integrity and an extracted-source simulator collection are checked
during packaging; the latter does not require BLE or TUI dependencies.

## What remains unvalidated

1. BLE discovery/connection and required ELM commands on the user's actual
   Vgate iCar Pro 2S and macOS environment.
2. Availability, raw identities and responses of the installed 5F/19/01 ECUs.
3. A real feature map for the exact MHS2 part/SW/HW/ASAM variant. No shipped
   definition permits live writes; demo definitions describe synthetic fields.
4. Any physical write or restore, including a benign short record. There is
   no outgoing long ISO-TP implementation in this version.
5. Persistence after an ignition cycle and recovery from firmware or hardware
   failure. A configuration snapshot does not cover flash/EEPROM or every DID.

The next validation step is the read-only `collect` procedure in README.md.
Use its raw evidence for the tasks in [CODEX_HANDOFF.md](CODEX_HANDOFF.md).
See [REVIEW.md](REVIEW.md) for the completed review and all seven follow-ups.
