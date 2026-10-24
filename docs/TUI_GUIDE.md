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
| F7 | Library |
| / | Focus the current section's search, when a text input is not active |
| Esc | Clear the current search and return to its table; family/category/ECU/status filters stay selected |
| ? | Open keyboard help, outside text inputs |
| Tab / Shift+Tab | Move between controls |
| Arrow keys | Move the table cursor; left/right also reveal columns outside the viewport |
| Enter | From a search input, focus results; from a row, select it or open Library details |
| Page Up / Page Down | Scroll a table or the focused Library details panel |
| Ctrl+Q / Ctrl+C | Request exit; an active operation is allowed to finish first |

Mouse selection also works. Scroll within a section to reach controls below
its visible area. The status line reports the current action, result or error.
On Macs configured to use the top-row keys for media controls, hold Fn when
using F1-F7, or use the tabs with the mouse.

The bright cyan row and table border identify keyboard focus. A muted cursor
keeps your place while you type in another control. Filtering retains the same
record when it remains in the results; otherwise the first matching row becomes
current. An empty result set clears the cursor and details. Search fields keep
normal typing, including `/`, `?` and `q`.

Function keys move focus into the chosen view. After an operation finishes,
focus returns to its control or to the result view. At narrow terminal widths,
rows of inputs and buttons stack vertically; use Tab and Shift+Tab to reach
controls below the viewport.

## Choose the source

Without an initial CLI source, **Devices** is the first section.

- **Scan Bluetooth** discovers nearby BLE devices, named devices first and
  strongest signal first. Filter the table and press Enter on the intended
  adapter to use it. The row supplies its exact address/UUID, so devices with
  the same display name stay distinguishable.
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

## Research MHS2 candidates

**Library (F7)** is an offline research catalog for MHS2 coding, adaptations and
related procedures. It searches names, reported channels and DIDs, source
metadata, limitations and verification questions. Combine the search with a
family, category and evidence-status filters to narrow the table. Choose MHS2
for explicitly scoped entries. The All options remove individual filters;
Esc clears only the search text. `tui --library FILE` uses the same browser for
another catalog with the research-library schema.

Move the cursor to inspect a candidate. Press Enter to focus its details panel,
then use arrows or Page Up/Page Down to read the full evidence. Tab and
Shift+Tab move between the table and panel. Esc returns to the table. Each
detail includes:

- The claimed ECU, software family and platform scope.
- Reported locators and values, with unknown DIDs, bytes, bits and masks left
  explicitly unknown.
- Prerequisites, limitations and questions that remain to be checked on the
  exact vehicle.
- Source titles, URLs, access coverage, family/identity evidence and the
  specific claims they support. Available source notes and independence groups
  preserve failed-access limits and shared evidence lineage.

Evidence statuses describe the available sources:

| Status | Meaning in the Library |
|---|---|
| documented | The cited material documents the claim; applicability to this vehicle still needs verification. |
| reported | A source reports the behavior or setting; verify the exact hardware and software variant. |
| conflicting | Claims or variants differ, and the disagreement remains unresolved. |
| out of scope | Related background outside the intended MHS2 coding scope. |

These statuses do not say whether a feature is currently enabled, installed or
supported in the vehicle. A forum label, IDE/ENG channel name or internal dataset
address does not establish a UDS DID or a coding byte. Source access is shown as
`full`, `excerpt` or `blocked` so the limits of the evidence remain visible.

Browsing and selecting a Library candidate never opens the diagnostic link,
changes the write editor, or prepares or applies a plan. A research entry cannot
authorize a write. Use **Explore** for values actually captured from a snapshot
and **Dictionary** for the separate definitions used by decoding and guarded
planning.

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
