# Release validation: 0.2.0

Validation date: 2026-10-24. This release moves the existing application to uv,
adds a navigable English Textual workspace, and expands the read-only dictionary
catalog. It preserves the 0.1 service and transport safety boundaries.

## Local results

| Check | Observed result |
|---|---|
| Locked install | `uv sync --locked --dev` succeeded; 32 packages resolved |
| Lock consistency | `uv lock --check --offline` succeeded |
| Final complete automated suite | 173 tests and 42 subtests passed in 64.89 seconds |
| Final focused TUI suite | 24 tests passed in 57.84 seconds |
| Ruff | `All checks passed!` after all source changes |
| CLI collection | 5F, 19 and 01 simulator captures completed; local support ZIP created |
| Entry points | `audi-a4-patcher --version` and `audi-workbench --version` both report 0.2.0 |
| Packaging | Wheel and source distribution built; dictionary JSON and both entry points verified |
| Documentation | Local Markdown links resolved; application, tests and documentation contain no Cyrillic text |
| Whitespace | `git diff --check` passed |

The final complete suite ran after the snapshot-browser simplification and all
review follow-ups. The simplification keeps only table metadata in the snapshot
list; opening a snapshot still reloads and verifies its artifact through the
existing inspection path. Both retained review findings and the snapshot-history
testing gap are closed; see [REVIEW-0.2.md](REVIEW-0.2.md).

Observed final test command: `uv run --locked --no-sync pytest -q --tb=short`,
after the locked development installation. The final package build used
`uv build --offline --quiet` with the populated local build cache.

Local environment: Linux x86_64, Python 3.12.14, uv 0.13.4, Bleak 2.1.1,
Textual 7.5.0, pytest 9.1.1, pytest-asyncio 1.4.0 and Ruff 0.17.2.
The cross-platform dependency resolution is committed in `uv.lock`.

## Added behavioral coverage

- Launching without a subcommand opens the TUI, with the selected source or an
  offline device picker. The explicit `tui` subcommand remains available.
- Bluetooth discovery, duplicate device names, exact UUID selection, empty
  scans, permission errors, and recovery to usable controls.
- Busy-operation ownership: queued duplicate scans and source changes do not
  replace a running operation or change its link.
- Keyboard tab navigation, search focus, input punctuation, help dismissal,
  snapshot selection, and raw-record selection.
- Selecting a new source, snapshot or dictionary entry invalidates old plans
  and clears write consent and confirmation.
- Manual editing of all eight plan-context inputs invalidates stale consent;
  search and confirmation inputs preserve the loaded plan. These paths send
  no writes and create no transaction receipts.
- Bluetooth names containing OSC/CSI and other terminal controls render as
  inert escaped text; raw discovery records and exact UUID selection survive.
- Invalid and unreadable saved artifacts do not prevent browsing valid history;
  failed inspection restores usable controls. Trace callbacks after shutdown
  neither raise nor advance the evidence offset.
- Dictionary metadata validation, opt-in read behavior, exact source labels,
  and explicit optional capture followed by decoding with no writes.

The UI was exercised headlessly at 120x40, 100x32 and 80x30. Actual Textual
screenshots were rendered and inspected for Explore, Dictionary and Devices.
`tui-preview.png` shows synthetic simulator data, not a vehicle capture.

## CI and physical validation

The committed GitHub Actions workflow runs locked installation, lint, the full
suite, simulator collection and packaging on Linux and macOS with Python 3.12.
Remote run results must be read from GitHub Actions; local results do not imply
that remote CI has run.

No physical Mac, Bluetooth adapter or vehicle was used during local validation.
All 18 bundled physical-vehicle definitions remain read-only; the two writable
recipes are synthetic. There is no verified CarPlay activation mapping, outgoing
long ISO-TP sender, SecurityAccess implementation, or firmware recovery image.

## Post-publication validation

For the first installation, the maintainer should confirm that the default
command reaches Devices, the simulator capture can be browsed and searched, and
the Actions jobs finish successfully for the published commit. The owner of the
vehicle performs the separate read-only `collect` procedure in README.md.

Expected evidence is a local capture/export containing exact ECU identities and
successful records or explicit per-record errors. A selected device banner is
not evidence of an established connection. If Bluetooth discovery or capture
fails, retain the local log and report the error; do not enable writes to
diagnose a transport failure. A regression in source ownership, consent
invalidation, backup persistence or write-count guards blocks use of writes
until corrected. No telemetry or automatic uploads are configured.
