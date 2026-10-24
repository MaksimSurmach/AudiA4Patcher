# Codex verification prompt

Paste the following prompt into Codex while the repository is open. Attach or
point it to local capture files and any original VCDS adaptation exports when
available. It can complete source/code review without vehicle access and must
report that boundary explicitly.

```text
Review and improve this AudiA4Patcher checkout end to end. Read AGENTS.md,
README.md, docs/LIBRARY.md, docs/DICTIONARY.md, docs/ARCHITECTURE.md and
docs/SOURCES.md first. Preserve existing user edits and use uv with the committed
lock. Keep application text, docs, research data and comments in English.

Target context: Audi A4 B9 2017 / MLB evo, Delphi MHS2 5F, Vgate iCar Pro 2S BLE,
macOS. Prior owner context identifies 8W0 035 193 G, MU-HS-LN-EU,
MHS2_ER_AU_P0470 / MU0191, EV_MUHigScl6C3DELP and USB 8W0035736. This is not a
fresh scan. Prior context also identifies cluster 8W5 920 741 A / PR 9S5,
monochrome; color-display recipes need separate hardware applicability checks.
Obtain actual HW/SW/ASAM identity from local evidence before calling
anything applicable. Do not substitute MHI2, MH2P, MIB3, generic MQB or legacy
MMI 3G/RMC instructions. EV_MUStd4CDELP alone does not establish MHS2.

1. Establish a baseline. Record git status, branch and commit. Run:
   uv sync --locked --dev
   uv run --locked pytest -q
   uv run --locked ruff check audi_workbench tests
   uv run --locked audi-a4-patcher --data-dir ./demo-review --demo collect
   uv run --locked audi-a4-patcher library
   uv build
   Confirm the built wheel contains both data JSON files. Simulator tests prove
   software behavior only. Reproduce any discovered bug with a meaningful test,
   then fix it without weakening existing write or protocol guards.

2. Audit every entry in audi_workbench/data/mhs2_library.json, not just a sample.
   Use its source URLs, access status and post locators. Open the original
   material and follow credible primary-source leads across Ross-Tech,
   AudiWorld, Audizine, A4 Klub, Audi-Sport and original MHS2 researchers.
   Prefer exact-unit adaptation maps and first-party diagnostic documentation.
   Mark inaccessible pages or excerpt-only evidence honestly. Do not bypass
   access controls. Reposts are not independent corroboration.

   For each entry check family, platform, part/HW/SW/ASAM, exact named channel,
   IDE/ENG identifiers, zero-based byte/bit, number base, reported values, outcome,
   hardware/licensing prerequisites, contradictions and source lineage. Verify
   ambiguous USB byte-19 descriptions without overwriting unrelated bits.
   Dataset addresses and IDE/ENG labels must never be converted into UDS DIDs
   by guesswork. A source-stated DID still needs a proven length and codec.
   Distinguish an observed baseline from an instruction to change a setting.

3. Inspect local evidence. Start with:
   uv run --locked audi-a4-patcher --data-dir ./runs/first list
   uv run --locked audi-a4-patcher inspect SNAPSHOT.json
   uv run --locked audi-a4-patcher library --snapshot SNAPSHOT.json --output ./review.json
   If a readable adaptation export is available:
   uv run --locked audi-a4-patcher library --snapshot SNAPSHOT.json --map ./adp-5F.csv --output ./map-review.json

   Use actual filenames from the artifact list. The library report observes
   bytes and labels, not confirmed feature states. Manually check that the CSV
   header belongs to the same ECU and capture context as the snapshot. Keep raw
   evidence unchanged and record its checksum. Readable CSV is not an adphex
   restore file. A partial snapshot is not a full 5F firmware/EEPROM backup.

4. If vehicle access is available and a parked, powered vehicle is ready for the
   user's requested read session, use the existing CLI only:
   uv run --locked audi-a4-patcher scan
   uv run --locked audi-a4-patcher --data-dir ./runs/first --device EXACT_SCAN_ID collect
   uv run --locked audi-a4-patcher --data-dir ./runs/first --device EXACT_SCAN_ID capture --ecu 5F --dtc

   Select the actual adapter from scan output; do not assume a name. Respect
   Bluetooth permissions and stop on fatal framing/link errors. Inspect each
   record and error: complete means the planned requests finished, not that
   every record succeeded. Additional reads require individually evidenced
   DIDs and explicit capture --did or --dids-file; do not sweep unknown ranges.
   If no car or adapter is available, finish all offline work and list exactly
   which hardware evidence is missing.

5. Test the TUI as a keyboard user at ordinary and small terminal sizes. Check
   F1-F7, Tab/Shift+Tab, visible focus versus retained row selection, arrow keys,
   Enter, / search, Esc clear, empty filters and long source details. Text inputs
   must retain literal /, ? and q. Filtering must not leave stale action targets.
   Selecting a library item must never prepare/apply a change or connect. Busy
   operations must keep sole ownership of their link and restore usable focus.

6. Update the research library with supported corrections and additions. Preserve
   provenance and uncertainty; remove or quarantine disproven mappings with a
   reason. Add read-only executable decoders only when exact raw identity,
   payload and semantics are independently supported. Keep private dictionaries
   and real VIN/serial/coding/map/capture/support ZIP data out of public commits.
   Public tests must be synthetic. Do not claim every MHS2 adaptation is covered.

7. Do not write to the real vehicle, bypass the raw whitelist or TTY guard, call
   Workbench.apply directly as a workaround, brute-force SecurityAccess, reset,
   clear faults, flash firmware, alter licenses or run downloaded patch scripts.
   Any future real change requires a separately reviewed definition and plan;
   a person applies it with the existing backup/confirmation flow. Keep the
   four-byte BLE value limit and long-coding block until independently engineered
   and verified. Never automatically retry an ambiguous write.

Deliver a local verification report and the code/data diff. Include:
- baseline/reviewed commit, exact test commands/results and hardware access status;
- counts by research status, family, source access and observed target coverage;
- a per-entry verdict: supported observation, reported only, conflicting,
  wrong family, inaccessible, or insufficient target evidence;
- source URL/post, claimed locator, observed value, exact variant evidence,
  missing DID/codec/enum/session details and next check for every unresolved entry;
- fixed defects, TUI navigation results and remaining concrete limitations;
- any local read-only dictionary candidates, with rationale and synthetic tests.

Finish all useful offline checks even if hardware is unavailable. Do not turn
missing evidence into enabled/disabled labels or verified/writable status.
Leave commits/publishing for a separate explicit instruction after this review.
```
