# Audi A4 Patcher

A local diagnostic workspace for **Audi A4 B9 2017 / MLB evo**, the **5F / MHS2**
infotainment unit, and the existing **Vgate iCar Pro 2S BLE** adapter. It combines
an English [Textual](https://textual.textualize.io/) terminal interface, a CLI,
editable JSON dictionaries and a persistent simulator.

The project uses **uv**, Python 3.11+ and a committed cross-platform `uv.lock`.
The development Python version is 3.12. macOS is the intended vehicle-side
platform; an iPhone is not a runtime target for this Python application.

**Hardware status:** the software and simulator can be exercised without a car.
The intended adapter and vehicle still need physical validation. Shipped live
vehicle definitions are read-only. Outgoing long-coding writes are blocked.

![Textual interface with synthetic simulator data](docs/tui-preview.png)

## Start on macOS

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), for example
with Homebrew:

```bash
brew install uv
```

Then clone the project and install its locked dependencies:

```bash
git clone https://github.com/MaksimSurmach/AudiA4Patcher.git
cd AudiA4Patcher
uv sync --locked
uv run --locked audi-a4-patcher
```

The default command opens the TUI. Without a selected source, it starts at
**Devices**. Bluetooth and TUI dependencies are included in the default install;
there are no extra groups to remember. uv can install the pinned Python version
if it is missing. An explicit `tui` subcommand also works.

To explore without Bluetooth:

```bash
uv run --locked audi-a4-patcher --data-dir ./demo-first --demo
```

No virtual-environment activation is needed. Dependency installation needs
internet access; diagnostic work and evidence storage remain local. The older
`audi-workbench` command remains an alias for the same application.

## Navigate the workspace

| Section | Purpose |
|---|---|
| **Devices** | Scan Bluetooth, filter discoveries, choose an exact adapter, or select Simulator / Offline |
| **Capture** | Select an ECU, read its records and browse saved snapshots |
| **Explore** | Search decoded fields and raw records; compare two snapshots |
| **Changes** | Prepare an edit, review the exact bytes, apply once, restore or reconcile |
| **Dictionary** | Search definitions, filter by ECU, inspect sources and fields, select an entry for an edit |
| **Evidence** | View session events and export a local support ZIP |

Use **F1–F6** to move between sections, **/** to focus the current search,
**Esc** to clear a search or return focus, and **?** for help. **Tab** and
**Shift+Tab** move between controls; tables support arrow-key selection and
**Enter**. **Ctrl+Q** requests exit; active diagnostic work must finish first.

Selecting a Bluetooth device chooses a target. It does not claim a connection
has succeeded. Each online operation opens the adapter, performs its work, then
closes it. The mode banner distinguishes Bluetooth, Simulator and Offline.

Detailed workflows and shortcuts: [TUI guide](docs/TUI_GUIDE.md).

## Try a complete change and restore

Use a fresh simulator data directory for the following example:

1. In **Capture**, choose **5F** and press **Capture**.
2. Open **Dictionary**, find `demo.display`, and use it in **Changes**.
3. Set byte `0`, bit `0`, value `0`, then press **Preview**. The fresh simulator
   starts with this synthetic flag set to `1`.
4. Review the before/after bytes, enable this one write and enter the exact
   confirmation phrase shown in the plan. Press **Apply once**.
5. Use **Restore preview** on the resulting transaction, review the reverse
   plan and apply it with its new confirmation phrase.

The simulator saves state across restarts. If the flag is already `0`, change
it to `1` or choose a new data directory. `demo.*` definitions describe invented
fields and never authorize writes to a physical ECU.

## First collection from the vehicle

Park the vehicle and switch on the ignition. Close Car Scanner or any other app
using the adapter. Allow Bluetooth access for the terminal in macOS privacy
settings.

In **Devices**, use **Scan Bluetooth**, select the adapter row and press
**Use device**. The selection uses the returned address/UUID. On macOS this
identifier normally looks like a UUID rather than a MAC address.

The CLI provides the same discovery step:

```bash
uv run --locked audi-a4-patcher scan
```

Use the exact identifier or a unique name from the result. The earlier adapter
advertisement was `IOS-Vlink`; substitute the current result if it differs:

```bash
uv run --locked audi-a4-patcher --data-dir ./runs/first --device IOS-Vlink collect
```

`collect` reads **5F, 19 and 01** in separate connections, preserves successes
and errors, then creates a support ZIP under `runs/first/exports/`. It does not
write coding or adaptation values. Keep the ZIP for further dictionary work;
it may contain VIN, serial numbers and raw diagnostic traffic. Nothing is
uploaded automatically.

To read only 5F:

```bash
uv run --locked audi-a4-patcher --data-dir ./runs/first --device IOS-Vlink capture --ecu 5F
```

The initial read set includes identification records, raw coding `0600` and
experimental `22AD`. The meaning of `22AD` is unverified; it is not labeled as
Apple_DIO or a CarPlay activation state. A record that the ECU does not support
is retained as an error, not a successful empty value.

### Additional explicit reads

```bash
uv run --locked audi-a4-patcher --data-dir ./runs/extra --device IOS-Vlink capture --ecu 5F --did F18C --did F17C
uv run --locked audi-a4-patcher --data-dir ./runs/extra --device IOS-Vlink capture --ecu 5F --dids-file examples/extra-dids.txt
```

A capture permits at most 256 requested identifiers including its normal and
identity reads. There is no automatic sweep of all possible addresses. Live
requests are paced. `--only` omits dictionary defaults while keeping identity
reads; `--dtc` reads trouble-code status without clearing it.

## Dictionaries and decoded values

The definitions are part of the repository in
[audi_workbench/data/default.json](audi_workbench/data/default.json). Each entry
carries a name, ECU and DID, confidence, sources, and optional fields. Writable
entries additionally require an exact vehicle/block fingerprint, length,
allowed-bit mask and evidence. All shipped physical-vehicle entries are
read-only; the writable examples are isolated to the simulator.

The bundled [dictionary catalog](docs/DICTIONARY_CATALOG.md) lists 18 live
read-only definitions and two simulator recipes. Eight additional identification
records are opt-in reads: their labels are available for decoding, while the
initial automatic capture set stays unchanged.

The **Dictionary** section exposes these definitions in English. **Explore**
shows decoded values next to raw data. Unknown fields and enum values remain
unknown rather than being displayed as disabled. Byte offsets start at **0**;
bit **0** is the least significant bit.

Generate a dictionary draft from an actual snapshot:

```bash
uv run --locked audi-a4-patcher dictionary-template SNAPSHOT.json --output ./my-mhs2-dictionary.json
uv run --locked audi-a4-patcher --dictionary ./my-mhs2-dictionary.json inspect SNAPSHOT.json
```

Replace `SNAPSHOT.json` with the captured path. The generated file includes the
current definitions and read-only `candidate.*` entries with raw bytes, record
length, exact identity and snapshot provenance. It does not infer feature
semantics from changing bytes. `--dictionary` loads the specified complete file.

A real feature map needs sources matching the block's part number, software and
ASAM version. See the [dictionary contract](docs/DICTIONARY.md) and
[protocol sources](docs/SOURCES.md) before adding a writable definition.

## CLI reference

Global options such as `--data-dir`, `--device`, `--demo` and `--dictionary` go
**before** the subcommand. Commands print machine-readable JSON where applicable.

```bash
uv run --locked audi-a4-patcher inspect SNAPSHOT.json
uv run --locked audi-a4-patcher diff BEFORE.json AFTER.json
uv run --locked audi-a4-patcher dictionary
uv run --locked audi-a4-patcher --data-dir ./runs/first export
```

A raw offline edit produces a reviewable, blocked plan until an exact permitted
definition is selected:

```bash
uv run --locked audi-a4-patcher --data-dir ./runs/first plan SNAPSHOT.json --did 0600 --byte 12 --bit 2 --value 1
```

A simulator CLI change uses the same engine as the TUI:

```bash
uv run --locked audi-a4-patcher --data-dir ./demo-cli --demo capture
uv run --locked audi-a4-patcher --data-dir ./demo-cli plan SNAPSHOT.json --entry demo.display --byte 0 --bit 0 --value 0
uv run --locked audi-a4-patcher --data-dir ./demo-cli --demo apply PLAN.json --enable-writes --confirm 'EXACT PHRASE FROM THE PLAN'
```

Replace every placeholder path and copy the complete phrase from the generated
plan. The apply receipt records its backup and result. Restore prepares a new
plan; a second explicit apply performs the reverse change:

```bash
uv run --locked audi-a4-patcher --data-dir ./demo-cli restore TRANSACTION.json
uv run --locked audi-a4-patcher --data-dir ./demo-cli --demo apply RESTORE_PLAN.json --enable-writes --confirm 'EXACT PHRASE FROM THE RESTORE PLAN'
```

If a live result is uncertain, reconnect and read its actual state:

```bash
uv run --locked audi-a4-patcher --data-dir ./runs/first --device IOS-Vlink reconcile TRANSACTION.json
```

`reconcile` does not repeat the write or roll it back. Keep using the same data
directory: unresolved `attempting`, `unknown` and `verification_failed` receipts
block new writes to the same ECU and VIN within that history.

## Write checks and recovery limits

All writes pass through one service. It checks explicit confirmation, exact
identity and CAN addresses, dictionary integrity, allowed bits and enum values
before and after the edit, unchanged length, transport capacity, fresh current
bytes, adapter voltage of 12.3–15.0 V, zero speed and zero RPM. Missing condition
data blocks the write. Adapter voltage is an input measurement, not a guarantee
of stable power throughout the operation.

Before sending one `2E` request, the service saves and verifies a fresh backup
and durably records the attempt. It then checks the response and separately
reads the value back. A timeout or ambiguous response is preserved for
reconciliation; there are no automatic write retries or rollbacks. Process
locking covers one data directory. SHA-256 detects file corruption but does not
prove that a locally edited dictionary describes the correct vehicle.

| Capability | Current boundary |
|---|---|
| Configuration snapshots | Only successfully read records from the requested set; not a full 5F image |
| Restore | A reverse change to one previously modified permitted record; ECU must still respond |
| Live short writes | At most **4 value bytes**, plus the three-byte write service/DID prefix |
| Long incoming responses | ISO-TP assembly with length and sequence checks |
| Long outgoing coding writes | **Blocked**; a separate outgoing ISO-TP sender is still required |
| SecurityAccess-protected writes | No access-key procedure is implemented |
| CarPlay activation | No verified Apple_DIO mapping or one-click activation recipe |
| Firmware/EEPROM recovery | Not implemented |

ELM327 `CAF1` permits seven UDS request bytes in this implementation; `ATAL` is
not a long-message sender. A long `0600` value can be read, saved and edited in
a preview, but cannot be physically written or restored by this release.
Immediate matching readback establishes `verified`; persistence after an
ignition cycle requires another capture.

## Development and verification

```bash
uv sync --locked --dev
uv run --locked pytest -q
uv run --locked ruff check audi_workbench tests
uv run --locked audi-a4-patcher --data-dir ./demo-check --demo collect
uv build
```

The GitHub Actions workflow runs software tests, headless Textual tests, lint,
simulator collection and a build on Linux and macOS with Python 3.12. It does
not access a real adapter. When dependencies change, regenerate `uv.lock` with
`uv lock` and commit it together with `pyproject.toml`. The old
`constraints-tested.txt` is retained only as historical v0.1 validation evidence.

| Document | Contents |
|---|---|
| [TUI guide](docs/TUI_GUIDE.md) | Navigation, device selection and everyday workflows |
| [Dictionary contract](docs/DICTIONARY.md) | Definition format, evidence and writable masks |
| [Dictionary catalog](docs/DICTIONARY_CATALOG.md) | Included identifiers, categories and optional reads |
| [Architecture](docs/ARCHITECTURE.md) | Components, transaction states and failure handling |
| [Codex handoff](docs/CODEX_HANDOFF.md) | Concrete next tasks and physical validation |
| [Version 0.2 validation](docs/VALIDATION-0.2.md) | uv, UI, catalog, packaging and final software checks |
| [Version 0.2 review](docs/REVIEW-0.2.md) | Independent review and verified follow-up changes |
| [Initial validation record](docs/VALIDATION.md) | Version 0.1 test environment and evidence |
| [Initial review record](docs/REVIEW.md) | Version 0.1 review findings and their resolutions |
| [AGENTS.md](AGENTS.md) | Rules for continuing development |

Dependency workflow references: [uv project structure](https://docs.astral.sh/uv/concepts/projects/layout/),
[dependency groups](https://docs.astral.sh/uv/concepts/projects/dependencies/),
and [GitHub Actions integration](https://docs.astral.sh/uv/guides/integration/github/).
