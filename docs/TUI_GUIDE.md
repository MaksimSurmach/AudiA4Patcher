# TUI guide

Audi A4 Patcher uses Textual for its English terminal interface. Launch it with
`uv run --locked audi-a4-patcher`. The explicit `tui` subcommand remains available.

## Navigation

| Key | Action |
|---|---|
| F1 | Devices |
| F2 | Capture |
| F3 | Explore |
| F4 | Changes |
| F5 | Dictionary |
| F6 | Evidence |
| / | Focus the current section's search, when a text input is not active |
| Esc | Clear the active search or return focus |
| ? | Open keyboard help, outside text inputs |
| Tab / Shift+Tab | Move between controls |
| Arrow keys / Enter | Navigate and select table rows or controls |
| Ctrl+Q / Ctrl+C | Request exit; an active operation is allowed to finish first |

Mouse selection also works. Scroll within a section to reach controls below
its visible area. The status line reports the current action, result or error.
On Macs configured to use the top-row keys for media controls, hold Fn when
using F1-F6, or use the tabs with the mouse.

## Choose the source

Without an initial CLI source, **Devices** is the first section.

- **Scan Bluetooth** discovers nearby BLE devices. Filter the table, select the
  intended adapter, then press **Use device**. The selected row supplies its
  exact address/UUID, so devices with the same display name stay distinguishable.
- A known UUID/address or unique advertised name can also be entered manually.
- **Simulator** selects the persistent synthetic ECU; no Bluetooth is used.
- **Offline** permits inspection, dictionary browsing and planning without a link.

A selected target is not a successful connection. The next online action opens
the adapter and closes it after completion. The banner shows the active mode.
Changing the source clears the previous write confirmation. Source selection
and other conflicting controls are disabled while an operation is active.

If discovery fails, the error is shown in the status and event log. Check
macOS Bluetooth permission and close other apps holding the adapter. An empty
scan or a device selection does not establish vehicle compatibility.

## Capture and browse snapshots

In **Capture**, choose the ECU, optionally supply explicit additional DIDs, and
press **Capture**. The default reads include identification and selected known
records. The options can limit the extra reads or include DTC status; no DTCs
are cleared.

The saved-snapshot table can be filtered and selected with the keyboard or
mouse. **Open** supports a path outside the current data directory, while
**Latest** selects the most recently saved snapshot in the current directory.

A partial capture retains completed records and the error that stopped the
session. Read failures remain visible beside successful records. A snapshot
covers its recorded set, not every configuration channel in the ECU.

## Explore raw and decoded data

**Explore** displays identity, known fields and raw records. Filter by a label
or DID to narrow the view. Decoded labels depend on the exact loaded dictionary;
unknown data remains explicitly unknown.

Select a raw record to prepare an offline edit in **Changes**. This selection
does not authorize a physical write. To compare captures, provide the second
snapshot path and use **Compare**; the first snapshot remains the opened one.

## Inspect the dictionary

Use **Dictionary** to search entries and filter them by ECU. Selecting an entry
shows its definition, sources, confidence and fields. **Use in changes** selects
that entry for a proposed edit. It does not make a read-only entry writable.

The built-in definitions are checked into the repository. To load a different
complete dictionary, start the app with:

```bash
uv run --locked audi-a4-patcher --dictionary ./my-mhs2-dictionary.json
```

For the schema and provenance requirements, read [DICTIONARY.md](DICTIONARY.md).

## Prepare and apply a change

1. Capture or open a snapshot.
2. Select a dictionary entry, or a raw DID for a blocked offline preview.
3. Enter a zero-based byte offset, an optional bit number and a value, or provide
   the full replacement hex value without changing its length.
4. Press **Preview**. Review the before/after bytes, changed bits, eligibility
   and any blocking reasons.
5. For an eligible operation, enable this one write, enter the exact phrase
   shown in the plan and press **Apply once**.

The service repeats its checks using fresh data, saves a backup and durable
receipt, sends one write, then reads back. The checkbox and confirmation clear
when the attempt finishes. A failed check restores the UI controls and retains
the error. Repeated presses do not start a second operation.

Live writes are limited to short, explicitly permitted records. The shipped
physical-vehicle definitions do not permit writes. Synthetic `demo.*` entries
are available for rehearsing the full flow.

## Restore, reconcile and export

After a verified change, **Restore preview** prepares a reverse plan for the
single changed record. Review and confirm that plan separately.

When a write result is uncertain, use **Reconcile** after reconnecting. It reads
identity and the current value without sending another write. Keep the same
data directory so the unresolved transaction history remains available.

**Evidence** shows session events and **Export support ZIP** saves an archive
of the selected data directory and loaded dictionary. Exports stay local and
may contain VINs, serial numbers and raw responses.

For transport limits and what a backup can restore, see the recovery section
in [README.md](../README.md) and [ARCHITECTURE.md](ARCHITECTURE.md).
