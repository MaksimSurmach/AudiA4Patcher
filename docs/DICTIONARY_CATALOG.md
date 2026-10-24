# Bundled dictionary catalog

The package includes one editable English JSON catalog at
`audi_workbench/data/default.json`: **20 definitions**, consisting of **18
read-only records** and **two synthetic simulator recipes**. It is included
in the source distribution and installed package; no external dictionary
download is required to start.

The catalog provides record names, source evidence, display encoding, category,
descriptions and default-read policy. It is not a complete MHS2 adaptation or
equipment map. No shipped definition authorizes writes to a physical vehicle.

## Source evidence

Except for the explicitly unknown `22AD` candidate and the synthetic recipes,
record names are checked against the original
[VW_Flash diagnostic-record table](https://github.com/bri3d/VW_Flash/blob/293f46d782bae2ced930497889b71eb782f122cb/lib/constants.py).
The eight additions below also use its
[ASCII/raw codec selection](https://github.com/bri3d/VW_Flash/blob/293f46d782bae2ced930497889b71eb782f122cb/lib/flash_uds.py).
Both references are pinned to source commit
`293f46d782bae2ced930497889b71eb782f122cb`, checked on 2026-10-24.

`source-confirmed` means that this source identifies the record and, where
specified, its text encoding. It does **not** mean the installed MHS2 implements
the record, or that the bytes form a verified feature map for this car. A
controller may reject an optional request; its response remains in the snapshot.

## Default read-only definitions

The following read set is unchanged from the initial release. The eight
identification labels apply to the available ECU profiles (`01`, `19`, `5F`).
The coding record and experimental candidate are restricted to `5F`.

| Entry ID | DID | Meaning / display | Evidence |
|---|---|---|---|
| `vag.vin` | `F190` | Vehicle identifier; ASCII | Source-confirmed |
| `vag.part_number` | `F187` | VAG spare-part identifier; ASCII | Source-confirmed |
| `vag.sw_version` | `F189` | Application software version; ASCII | Source-confirmed |
| `vag.hw_part_number` | `F191` | ECU hardware identifier; ASCII | Source-confirmed |
| `vag.hw_version` | `F1A3` | Hardware revision; ASCII | Source-confirmed |
| `vag.system_name` | `F197` | System or engine-type name; ASCII | Source-confirmed |
| `vag.asam_name` | `F19E` | ASAM/ODX diagnostic-description identifier; ASCII | Source-confirmed |
| `vag.asam_version` | `F1A2` | ASAM/ODX description version; ASCII | Source-confirmed |
| `vag.coding` | `0600` | Coding record; raw hex, with no verified MHS2 bit map | Source-confirmed record name |
| `mhs2.experimental_22ad` | `22AD` | Earlier probe candidate; raw bytes with unknown meaning | Unverified prior read-only probe |

The `22AD` candidate is not labeled as Apple_DIO or CarPlay enabled/disabled.
Its presence preserves the existing probe's explicit read set, not a semantic
claim about that address.

## Optional read-only definitions

All eight entries are `source-confirmed`, have `read_by_default: false`, and
remain available for explicit reads and offline decoding. They are not added
to an ordinary Capture or `collect` operation.

| Entry ID | DID | Meaning | Display |
|---|---|---|---|
| `vag.serial_number` | `F18C` | ECU serial identifier | ASCII |
| `vag.fazit_id` | `F17C` | VAG FAZIT identifier | ASCII |
| `vag.active_session` | `F186` | Current diagnostic-session record | Raw hex |
| `vag.workshop_system_name` | `F1AA` | Workshop-facing system name | ASCII |
| `vag.bootloader_id` | `F1F4` | Bootloader identification | ASCII |
| `vag.software_block_version` | `F1AB` | Logical software-block version | ASCII |
| `vag.production_change` | `F17E` | ECU production-revision identifier | ASCII |
| `vag.programming_information` | `F1DF` | Programming-information record | Raw hex |

ASCII is for display only: original hex, including padding, remains unchanged.
The raw session and programming records have no guessed byte layout or enum
mapping. Reading them does not switch sessions, enter programming mode or
grant restore permission.

For example, after selecting the actual adapter name or UUID from a scan:

```bash
uv run audi-a4-patcher --data-dir ./runs/extra --device IOS-Vlink \
  capture --ecu 5F --did F18C --did F17C --did F186
```

Replace `IOS-Vlink` if the scanner reports a different identifier. This command
requests the normal read set plus the three explicit records. Add `--only` to
read the required identity and those explicit records without the dictionary's
other defaults. `--dids-file examples/extra-dids.txt` is another supported input.

## Simulator recipes

| Entry ID | DID | Demonstrated behavior | Permission |
|---|---|---|---|
| `demo.display` | `D100` | Four-byte settings record with synthetic animation and brightness fields | Simulator only; exact synthetic fingerprint |
| `demo.long_coding` | `0600` | Twenty-byte value with a synthetic flag at byte 12, bit 2 | Simulator only; exact synthetic fingerprint |

These are examples of a reviewed field definition and reversible change, not
Audi coding instructions. Live decoding excludes them, and the write engine
rejects a simulator definition on a BLE connection. Simulator support for a long
value does not establish outgoing long ISO-TP support on the adapter.

## Inspect and extend

Open the Dictionary view in the TUI to search the bundled entries and inspect
their sources, fields, descriptions and permissions. The CLI prints the same
loaded catalog as JSON:

```bash
uv run audi-a4-patcher dictionary
uv run audi-a4-patcher inspect SNAPSHOT.json
uv run audi-a4-patcher dictionary-template SNAPSHOT.json --output ./my-mhs2-dictionary.json
uv run audi-a4-patcher --dictionary ./my-mhs2-dictionary.json inspect SNAPSHOT.json
```

Replace `SNAPSHOT.json` with a captured snapshot path. The generated file keeps
the loaded definitions and adds unknown read-only candidates with their exact
observed identity and source snapshot hash. It is a separate catalog:
`--dictionary` replaces the bundled selection rather than merging silently.

For a new real feature, first obtain a definition matching part number, software,
hardware and ASAM. Add read-only decoding and source evidence before considering
write permission. The complete schema and write requirements are in
[DICTIONARY.md](DICTIONARY.md). Catalog metadata alone never grants permission.
