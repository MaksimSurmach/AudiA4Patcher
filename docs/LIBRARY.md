# MHS2 coding and adaptation research library

The packaged library is a reviewable collection of named adaptations, coding
claims, observed configurations, prerequisites, failures and incompatible
recipes. It is designed for **Delphi MHS2 / MIB2 High Scale**, with related
platform evidence kept explicitly separate. It is not an acquired factory
ODX/ROD database or an exhaustive list of all firmware-dependent adaptations.

The initial release contains **54 entries across 13 categories and 38 source
records**, including 35 entries explicitly scoped to MHS2. The source count
includes related pages and historical leads, not 38 independent confirmations.
Eight entries document observations/tool rules, 33 are reports, six retain
conflicts or ambiguities and seven are outside the target scope.

The project target is an Audi A4 B9 2017. Earlier owner context identified
`8W0 035 193 G`, `MU-HS-LN-EU`, `MHS2_ER_AU_P0470 / MU0191` and
`EV_MUHigScl6C3DELP`. These are research selectors, not a fresh capture or a
complete raw identity fingerprint. No owner's VIN, serial number, coding dump,
adaptation map or support ZIP is included in this repository.

## Browse and compare

Open **Library (F7)** in the TUI. Search names, identifiers, channels, source
text or URLs; filter by family, category and evidence status. The detail panel includes
the proposed locator, reported values, prerequisites, limitations, source
access and questions to resolve. Selecting a library row only displays research.

The same data is available without opening Bluetooth or creating a data store:

```bash
uv run --locked audi-a4-patcher library
uv run --locked audi-a4-patcher library --query Apple_DIO
uv run --locked audi-a4-patcher library --family MHS2 --status conflicting
uv run --locked audi-a4-patcher library --entry ENTRY_ID
uv run --locked audi-a4-patcher library --snapshot SNAPSHOT.json --output ./review.json
uv run --locked audi-a4-patcher library --snapshot SNAPSHOT.json --map ./adp-5F.csv --output ./map-review.json
```

`library --file PATH` loads an alternative library with the same schema;
`tui --library PATH` opens it in the same terminal browser. Neither loads
that file as a write dictionary. `--output` creates a new file with mode `0600`
and refuses to overwrite an existing file. Comparison does not connect, scan,
change sessions, read new DIDs or write to a controller.

CSV support covers VCDS rows beginning with IDE/ENG/MAS identifiers and ordinary
`Channel,Value,Unit` files, separated by commas, semicolons or tabs. UTF-8 and
Windows-1252 are accepted. Parsed channel names, values and units retain their
exact text, including padding and quoted multiline line endings; normalization
is used only for matching. Each `row` is the record's starting physical line in
the original file. Unlabeled trailing VCDS fields are retained as `extra_fields`,
not guessed to be units or codecs. Duplicate matches are ambiguous; short-name
matches are explicitly tentative. Source header comments are not copied into
reports because they can contain a VIN. Comment-looking lines inside quoted
values remain data.

A recognized Channel/Value header selects its delimiter. Malformed data under
that header is an error; it is never reinterpreted using another delimiter.
Headerless maps with multiple plausible parses are rejected. To resolve one,
add a `Channel,Value,Unit` header using the correct delimiter to a working copy,
retaining the unchanged original for provenance. Import never changes its input.

**A CSV label match does not verify that a map belongs to the snapshot.** Keep
the original files together and verify their identity headers manually. The
report carries checksums for both inputs. The supported readable CSV is not a
VCDS `adphex` restore image, and the application does not restore one.

## What a comparison means

| Result | Meaning |
|---|---|
| `family_candidate` | Identity text suggests the stated family; exact variant and semantics still need review |
| `family_mismatch` | The entry states another family; do not transfer its layout |
| `family_unknown` | The input does not establish one recognized family, or its signals conflict |
| `ecu_mismatch` / `simulator` | Wrong ECU or synthetic data; no MHS2 vehicle confirmation |
| `raw_observed` | A saved record contains bytes at the candidate locator; the proposed meaning remains unverified |
| `label_observed` | A named CSV value matches a label or identifiers; no wire-level codec is established |
| `ambiguous` | Multiple map rows match; none is chosen automatically |
| `not_observed`, `not_found`, `read_failed`, `payload_too_short` | Missing or inadequate evidence; never interpreted as disabled |

All research report items retain `feature_state: "unverified"` and
`writable: false`. A bit value of `1` is a byte observation, not proof that
CarPlay, a camera, a menu or an audio feature works.

The family filter recognizes explicit family strings and the high-scale
`MU-HS` / `EV_MUHigScl6C3DELP` identity. `EV_MUStd4CDELP` by itself does not
establish MHS2. Other-family and generic evidence remains useful for research
but is not presented as an exact MHS2 match.

## Evidence model

The data file is
[`audi_workbench/data/mhs2_library.json`](../audi_workbench/data/mhs2_library.json).
Its schema is `audi-workbench.library/v1`. It contains version/scope metadata,
`index_base: 0`, reusable source records, and entries with claim-level evidence.

Each entry contains a stable `id`, `name`, `category`, `kind`, ECU address,
`families`, `platforms`, `status`, a summary, a locator, reported values,
prerequisites, limitations, verification questions and source references.
`locator.channel`, `did`, `byte`, `bit` and `mask` must be explicit; unknown
values are `null`. Byte and bit indices are zero-based; bit 0 is LSB.

| Status | Interpretation |
|---|---|
| `documented` | The referenced material documents a channel, observation or tool rule; this does not mean target-vehicle verification |
| `reported` | An author describes a procedure or outcome; exact applicability needs checking |
| `conflicting` | Failure, disagreement, ambiguous encoding or configuration dependence needs resolution |
| `out_of_scope` | The cited recipe explicitly concerns another family/platform or is unsuitable for this target |

Source records preserve URL, title, author/source type, inspected family and
identity evidence, and access as `full`, `excerpt` or `blocked`. Search snippets
and video descriptions are not treated as full map/video inspection. Reposts
are one evidence lineage, not independent confirmations. Broken references
remain marked, so a future review can retrieve or replace the evidence.

Named UI identifiers such as `IDE03471-ENG130249` are **not UDS DIDs**. Dataset
addresses such as `0x003000` are not DIDs either. A source that states a DID
still does not establish the complete payload layout, codec, session or access
requirements. The library never derives these missing details numerically.

## Verification and promotion

1. Establish the complete current part/HW/SW/ASAM identity and software train.
   Preserve raw capture bytes; distinguish prior owner context from current data.
2. Inspect the original source and its exact post, variant, date and outcome.
   Preserve contradictory reports and failed attempts. Use a correct factory
   definition when available; do not redistribute proprietary datasets.
3. Compare the source with the target's own named adaptation map and original
   coding. Record value, units, enums, payload length, and missing evidence.
4. Obtain a separately evidenced transport DID and codec. Verify any proposed
   read-only decoder against the exact raw fingerprint and a meaningful fixture.
5. Add reviewed read-only fields to a **local** executable dictionary first.
   Real identity fingerprints include private information and do not belong in
   public fixtures. Keep publicly committed tests synthetic and explicitly labeled.
6. A writable definition is a separate engineering decision governed by
   [DICTIONARY.md](DICTIONARY.md), fresh backup/readback and the transport limit.
   A research status, a forum success report or a Codex conclusion does not bypass
   those gates. No automatic library-to-write conversion exists.

The current BLE transport permits only four DID value bytes in an outgoing
write. Long coding and protected adaptations remain blocked. A snapshot backs
up only records actually read; it cannot recover firmware, EEPROM, certificates
or every setting in 5F. Existing licensing and hardware prerequisites must be
checked independently of coding.

Use [CODEX_VERIFY.md](CODEX_VERIFY.md) for the complete review prompt and the
required evidence/report format.
