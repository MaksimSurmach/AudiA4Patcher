# Audi A4 Patcher

Target: user's Audi A4 B9 2017 (MLB evo), MHS2 5F, Vgate iCar Pro 2S BLE, macOS.
Python 3.11+. Never imply hardware validation from simulator tests.

Use uv for dependency management. Keep pyproject.toml and uv.lock consistent;
generate the lock with uv, never edit it by hand. Run `uv sync --locked` to
install. The default dependencies include Bleak and Textual; developer tools
belong to the `dev` dependency group. The command is `audi-a4-patcher`; the import
package remains `audi_workbench`.

Write application text, documentation, dictionary labels and code comments in
English. Preserve raw vehicle values exactly even when their contents differ.

Every vehicle read/write path goes through DiagnosticLink; all writes go through
Workbench.apply. Default read-only. Do not add automatic retries of writes, blind
SecurityAccess guessing, resets, flashing, DTC clearing, or arbitrary raw write UI.
No MQB-to-MLB coding byte guesses. Unknown means unknown.

The research catalog (library/v1) is passive evidence, separate from executable
dictionary/v1 permissions. Never promote a forum status, family match, named CSV
observation or IDE/ENG identifier into a verified feature or wire-level address.
Preserve source access/lineage, failed outcomes and unresolved encoding. Library
browsing and comparison must remain offline and must not expand automatic reads.

Keep exact raw identities and provenance. A snapshot is a partial configuration
snapshot, not an EEPROM/flash recovery image. Readable does not imply writable.
Byte/bit numbering is zero-based, bit 0 is LSB.

Use Textual's existing controls, event handlers and worker model for UI work.
Keep keyboard navigation and filtering usable without a mouse. Selecting an
adapter does not establish a connection; online operations open and close it.
Do not switch the active source or cancel an operation while it owns the link.

Run `uv run --locked pytest -q`, `uv run --locked ruff check audi_workbench tests`
and a simulator CLI collection. Add behavioral tests for write guards, protocol
framing, recovery failures and new UI interactions. Do not test implementation
trivia. No network or hardware in tests. Respect ownership boundaries during
parallel work. Never commit real vehicle captures or their VINs, serial numbers
and support ZIPs. Keep simulator fixtures clearly labeled as synthetic.

To operate the tool, use the JSON CLI (see README "Agent use"); start with
`list` to find artifacts. Use `raw` for ad-hoc reads and `capture --did` for
evidence worth keeping. Never work around the read-only `raw` whitelist or the
interactive-terminal guard on real-vehicle `apply`; hand the plan and its
confirmation phrase to the user.
