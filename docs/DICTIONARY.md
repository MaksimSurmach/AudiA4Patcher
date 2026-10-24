# Dictionary contract

Top-level `schema` is `audi-workbench.dictionary/v1`; `entries` is an array.
`--dictionary FILE` loads that complete file. It does not silently merge files.
`dictionary-template` includes the current definitions plus read-only candidates.

Each entry has `id`, `name`, `ecu`, four-character uppercase `did`, `confidence`
and a nonempty `source` array. Identity labels may use `ecu: "*"`.
`encoding` is `ascii` or `hex`. Data is preserved as raw hex, regardless of display.

Optional catalog metadata:

- `category`: a nonempty English label used to organize definitions in the UI.
- `description`: a nonempty English explanation of the record and its limits.
- `read_by_default`: a boolean, defaulting to `true` for backward compatibility.
  `false` keeps a definition available for inspection and decoding without adding
  its DID to the dictionary's automatic read list. An explicit `--did` or
  `--dids-file` request can still read it. The service always reads its required
  identity records independently of this setting.

These fields do not authorize writes or prove that a controller supports a DID.
For the actual shipped definitions and their sources, see
[DICTIONARY_CATALOG.md](DICTIONARY_CATALOG.md).

Feature definitions need a complete exact `applies_to` fingerprint. All identity
values are raw hex captured from the ECU, including padding. Required keys:
`vin`, `part_number`, `sw_version`, `hw_part_number`, `asam_name`, `asam_version`.
The dictionary can include additional exact identity keys.

`fields` contains single-byte fields with `name`, zero-based `offset`, `mask`
(integer 1..255), and optional `values`, a decimal-string-to-label mapping.
The displayed value is `(byte & mask) >> least_significant_set_bit(mask)`.
Use contiguous masks for compact numeric fields. Full records may span many
bytes, but multi-byte numeric field codecs are not implemented in this release.

Example of a **synthetic** field, not an Audi coding instruction:

```json
{"name": "DEMO ONLY: animation", "offset": 0, "mask": 1,
 "values": {"0": "disabled", "1": "enabled"}}
```

Writable definitions additionally require:

- `writable: true`, `verification: "locally-verified"` and `origin: "ble"` or
  `"demo"`; the origin must match the connection.
- Exact `ecu`, integer `tx`/`rx`, full fingerprint and exact payload `length`.
- `allowed_mask`: one hex byte per payload byte. Only set bits may change.
- Explicit `session: null` or `3`; only default/unprotected and extended-session
  unprotected writes are implemented. `security` must equal `"none"`.
- Nonempty source and meaningful `evidence` describing how the definition was
  checked. `unknown` and `experimental` entries cannot authorize writes.
- Valid enum values for all declared fields both before and after the edit.
  An unknown original value blocks the change, because this definition could
  not authorize restoring that original value later.

The loader checks structure and the write engine repeats validation. A local
review flag is a human assertion; software cannot prove a web page describes
your hardware. The shipped dictionary contains no BLE-writable definitions.

## Confidence and availability

`confidence` describes the evidence: `standard`, `source-confirmed`,
`vehicle-observed`, `experimental`, `unknown` or `demo`. A successful read shows
that a value was available; it does not prove its semantic meaning or writability.

Decoder rows distinguish `decoded`, `raw`, `unknown` and `unavailable`.
Identity/length mismatches never produce a misleading feature enabled/disabled
label. Unknown enum values remain unknown.

## Extending the dictionary from a capture

1. Keep the original support ZIP and snapshot unchanged.
2. Generate a draft with `dictionary-template`.
3. Find a definition matching part/SW/HW/ASAM. Save source links and exact
   excerpt/record identifiers in your notes.
4. Add read-only field definitions first. Inspect original raw bytes next to
   labels and compare independent evidence.
5. A separate reviewed entry may become writable only when byte layout, allowed
   values, diagnostic session and access procedure are known. Generate a new
   plan after any change to that entry.

CSV reports with labels alone are not automatically converted to raw DID writes.
ODX/ROD parsers, arbitrary executable plugins and seed/key plugins are not part
of this release. Keep prospective importers data-only and fail closed.
