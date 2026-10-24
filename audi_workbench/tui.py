"""Terminal UI. A single non-cancelling operation owns the diagnostic connection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from .cli import hex_did
from .model import ECUS, WorkbenchError
from .simulator import DemoLink
from .store import Store
from .transport import BLELink, scan_devices

_TERMINAL_CONTROLS = {code: f"\\x{code:02x}" for code in (*range(0x20), *range(0x7F, 0xA0))}


def _device_cell(value: object) -> Text:
    """Escape terminal controls for display while preserving raw discovery values."""
    return Text(str(value).translate(_TERMINAL_CONTROLS))


class HelpScreen(ModalScreen):
    BINDINGS: ClassVar = [Binding("escape", "close_help", "Close", show=False)]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-dialog"):
            yield Label("Keyboard guide", classes="section")
            yield Static(
                "F1  Devices      Scan Bluetooth and select an adapter\n"
                "F2  Capture      Choose an ECU, read it, or open a saved snapshot\n"
                "F3  Explore      Search decoded fields and raw records\n"
                "F4  Changes      Preview, confirm, restore or reconcile a change\n"
                "F5  Dictionary   Search definitions, read evidence and select an entry\n"
                "F6  Evidence     Export a support ZIP and inspect session events\n\n"
                "/       Focus the current view's search\n"
                "Esc     Clear search and return to its table\n"
                "Tab     Move between controls; Shift+Tab goes back\n"
                "Arrows  Move through rows; Enter selects a row\n"
                "?       Open this guide\n"
                "Ctrl+Q  Quit when no operation is running\n\n"
                "Text inputs keep normal typing, including /, ? and q.\n"
                "Selecting a device does not connect. Each diagnostic operation\n"
                "opens its own connection, then closes it.\n"
                "Dictionary labels show evidence; unknown values remain unknown.", markup=False)
            yield Button("Close help", id="close-help", variant="primary")

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss()

    def action_close_help(self):
        self.dismiss()


class WorkbenchApp(App):
    TITLE = "Audi A4 Patcher"
    SUB_TITLE = "A4 B9 · MLB evo · 5F / MHS2"
    BINDINGS: ClassVar = [
        Binding("f1", "view('devices-tab')", "Devices", priority=True),
        Binding("f2", "view('capture-tab')", "Capture", priority=True),
        Binding("f3", "view('explore-tab')", "Explore", priority=True),
        Binding("f4", "view('change-tab')", "Changes", priority=True),
        Binding("f5", "view('dictionary-tab')", "Dictionary", priority=True),
        Binding("f6", "view('evidence-tab')", "Evidence", priority=True),
        Binding("slash", "focus_search", "Search", key_display="/"),
        Binding("escape", "clear_search", show=False),
        Binding("question_mark", "show_help", "Help", key_display="?"),
        Binding("ctrl+q", "safe_quit", "Quit", priority=True),
        Binding("ctrl+c", "safe_quit", show=False, priority=True),
    ]
    SEARCH_TARGETS: ClassVar = {
        "devices-tab": ("device-filter", "device-table"),
        "capture-tab": ("snapshot-filter", "snapshot-table"),
        "explore-tab": ("record-filter", "raw-table"),
        "dictionary-tab": ("dictionary-filter", "dictionary-table"),
    }
    EDIT_CONTEXT: ClassVar = ("snapshot", "entry", "target-did", "edit-byte", "edit-bit",
                              "edit-value", "edit-hex", "plan-file")
    CSS = """
    Screen { background: #111923; color: #e4ebf4; }
    Header { background: #1d2d40; }
    Footer { background: #1d2d40; }
    #mode-banner { background: #1c3545; color: #a5dfef; padding: 0 2; height: auto; max-height: 3; }
    #status { dock: bottom; margin-bottom: 1; height: 1; background: #1c2938; padding: 0 2; text-wrap: nowrap; text-overflow: ellipsis; }
    TabPane { padding: 1 2; }
    VerticalScroll { height: 1fr; }
    .row { height: auto; min-height: 3; margin-bottom: 1; }
    .row > Input { width: 1fr; margin-right: 1; }
    .row > Select { width: 1fr; margin-right: 1; }
    .row > Button { margin-right: 1; min-width: 14; }
    .section { color: #72c8dd; text-style: bold; margin: 1 0; }
    .hint { color: #9aaec2; height: auto; margin-bottom: 1; }
    Input { background: #172535; border: tall #3b5167; }
    Button { background: #233e54; }
    Button.-primary { background: #0b7d91; }
    Button.-warning { background: #885218; }
    DataTable { background: #152230; height: 12; margin-bottom: 1; }
    #feature-table { height: 15; }
    #capture-note, #snapshot-note, #plan-preview { height: auto; padding: 1; background: #172535; }
    #plan-preview { min-height: 5; max-height: 16; overflow-y: auto; margin-bottom: 1; }
    #logs { height: 1fr; min-height: 10; background: #0e151e; }
    #dictionary-table { height: 14; }
    #device-table { height: 10; }
    #snapshot-table { height: 9; }
    #device-note, #dictionary-detail { height: auto; padding: 1; background: #172535; }
    #dictionary-detail { min-height: 4; margin-bottom: 1; }
    #device-count, #snapshot-count, #record-count, #dictionary-count { height: auto; color: #9aaec2; }
    #dictionary-ecu { width: 22; }
    HelpScreen { align: center middle; background: #000000 65%; }
    #help-dialog { width: 90%; max-width: 88; height: auto; max-height: 90%;
                   padding: 1 2; border: round #72c8dd; background: #172535; }
    #help-dialog Button { margin-top: 1; }
    .short { width: 1fr; }
    Checkbox { margin-bottom: 1; }
    """

    def __init__(self, workbench):
        super().__init__()
        self.workbench = workbench
        self.busy = False
        self.snapshot_path: Path | None = None
        self.plan_path: Path | None = None
        self.transaction_path: Path | None = None
        self.last_error: str | None = None
        self._trace_seen = None
        self._trace_offset = 0
        self._ui_ready = False
        self._devices: list[dict] = []
        self._saved_snapshots: list[tuple[Path, str, str, str]] = []
        self._snapshot: dict | None = None
        self._selected_entry: str | None = None
        self._plan_context: tuple | None = None

    @property
    def mode(self) -> str:
        link = self.workbench.link
        if link is None:
            return "OFFLINE · inspection and planning"
        if link.kind == "demo":
            return "SIMULATOR · synthetic car data · no Bluetooth connection"
        target = getattr(link, "device", "explicit adapter")
        return f"BLUETOOTH · selected: {target} · opens per operation · hardware unverified"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.mode, id="mode-banner", markup=False)
        with TabbedContent(initial="capture-tab" if self.workbench.link else "devices-tab", id="tabs"):
            with TabPane("Devices", id="devices-tab"), VerticalScroll():
                yield Label("Choose a data source", classes="section")
                yield Static("Scan nearby Bluetooth devices and press Enter on the adapter row (usually IOS-Vlink). "
                             "F1–F6 switch views; / searches; Enter selects; ? opens help.", classes="hint")
                with Horizontal(classes="row"):
                    yield Button("Scan Bluetooth", variant="primary", id="scan")
                    yield Button("Simulator", id="simulator")
                    yield Button("Offline", id="offline")
                yield Input(placeholder="Search devices by name, UUID or service", id="device-filter")
                yield Static("No scan yet.", id="device-count", markup=False)
                yield DataTable(id="device-table", cursor_type="row")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Selected UUID/address, or an exact unique device name", id="device-address")
                    yield Button("Use device", variant="primary", id="use-device")
                yield Static("Selecting an adapter does not connect. Capture opens the connection and reads the ECU. "
                             "Close other diagnostic apps before connecting.", id="device-note", markup=False)
            with TabPane("Capture", id="capture-tab"), VerticalScroll():
                yield Label("Read and preserve the current configuration", classes="section")
                yield Static("Capture reads identity and known DIDs. Each record keeps its raw response. "
                             "A snapshot covers listed records only; it is not a firmware recovery image.", classes="hint")
                with Horizontal(classes="row"):
                    yield Select([(f"{key} · {ecu.name}", key) for key, ecu in ECUS.items()],
                                 value="5F", allow_blank=False, id="ecu")
                    yield Button("Capture", variant="primary", id="capture")
                yield Input(placeholder="Additional DIDs, comma-separated: 0600, 22AD", id="capture-dids")
                yield Checkbox("Only identity + the explicit DIDs above", id="only-dids")
                yield Checkbox("Read trouble-code status too (no clearing)", id="with-dtc")
                yield Static("Ready. Online actions open the adapter for one operation and then disconnect.",
                             id="capture-note", markup=False)
                yield Label("Saved snapshot", classes="section")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Path to a snapshot JSON file", id="snapshot")
                    yield Button("Open", id="inspect")
                    yield Button("Latest", id="latest")
                yield Input(placeholder="Search saved snapshots by filename, ECU or mode", id="snapshot-filter")
                yield Static("No saved snapshots.", id="snapshot-count", markup=False)
                yield DataTable(id="snapshot-table", cursor_type="row")
                yield Static("Select a saved row with Enter to open it. Devices (F1) changes the data source. "
                             "Real default definitions are read-only.", classes="hint")
            with TabPane("Explore", id="explore-tab"), VerticalScroll():
                yield Static("Open or capture a snapshot first.", id="snapshot-note", markup=False)
                yield Input(placeholder="Search records by DID, name, value or state", id="record-filter")
                yield Static("No snapshot open.", id="record-count", markup=False)
                yield Label("Known fields and unknown values", classes="section")
                yield DataTable(id="feature-table", cursor_type="row")
                yield Label("Raw records · select a row to prepare an offline edit", classes="section")
                yield DataTable(id="raw-table", cursor_type="row")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Second snapshot path to compare", id="diff-path")
                    yield Button("Compare", id="diff")
                yield DataTable(id="diff-table", cursor_type="row")
            with TabPane("Changes", id="change-tab"), VerticalScroll():
                yield Label("1 · Prepare an offline change", classes="section")
                yield Static("Byte and bit numbers start at 0. Bit 0 is the least significant bit. "
                             "Choose a dictionary entry or a raw DID. Raw edits stay previews until a definition is verified.",
                             classes="hint")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Entry ID, e.g. demo.display", id="entry")
                    yield Input(placeholder="OR raw DID, e.g. 0600", id="target-did")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Byte index", id="edit-byte")
                    yield Input(placeholder="Bit 0–7 (optional)", id="edit-bit")
                    yield Input(placeholder="Value (0/1 for bit)", id="edit-value")
                with Horizontal(classes="row"):
                    yield Input(placeholder="OR full replacement hex value", id="edit-hex")
                    yield Button("Preview", variant="primary", id="preview")
                yield Static("No change plan loaded.", id="plan-preview", markup=False)
                yield Label("2 · Review, confirm, then apply once", classes="section")
                yield Static("The live check verifies identity, current bytes, speed/RPM and voltage, then saves a backup. "
                             "BLE accepts at most 4 value bytes in this release; long coding remains read/preview only.",
                             classes="hint")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Plan JSON path", id="plan-file")
                    yield Button("Load plan", id="load-plan")
                yield Input(placeholder="Type the exact confirmation phrase shown in the preview", id="confirmation")
                yield Checkbox("Enable this one write after reviewing the plan", id="enable-writes")
                yield Button("Apply once", variant="warning", id="apply")
                yield Label("3 · Restore or reconcile a transaction", classes="section")
                yield Input(placeholder="Transaction receipt JSON path", id="transaction-file")
                with Horizontal(classes="row"):
                    yield Button("Restore preview", id="restore")
                    yield Button("Reconcile", id="reconcile")
                yield Static("Restore creates a new plan, never an automatic rollback. Reconcile reads the current "
                             "value after a timeout or interrupted write.", classes="hint")
            with TabPane("Dictionary", id="dictionary-tab"), VerticalScroll():
                yield Label("Definitions and their evidence", classes="section")
                yield Static("Browse shipped and imported definitions. A label does not prove that the ECU supports "
                             "the record. Simulator recipes never authorize a vehicle write.", classes="hint")
                with Horizontal(classes="row"):
                    yield Input(placeholder="Search DID, name, field, category or source", id="dictionary-filter")
                    yield Select([("All ECUs", "*")] + [(key, key) for key in ECUS],
                                 value="*", allow_blank=False, id="dictionary-ecu")
                yield Static("", id="dictionary-count", markup=False)
                yield DataTable(id="dictionary-table", cursor_type="row")
                yield Static("Select a definition to see fields, evidence and applicability.",
                             id="dictionary-detail", markup=False)
                with Horizontal(classes="row"):
                    yield Button("Use in changes", variant="primary", id="use-entry")
                    yield Button("Refresh dictionary", id="dictionary")
            with TabPane("Evidence", id="evidence-tab"), VerticalScroll():
                yield Button("Export support ZIP", variant="primary", id="export")
                yield Static("Exports stay on this computer and include unredacted VIN/serial numbers and raw logs.", classes="hint")
                yield Label("Live session events", classes="section")
                yield RichLog(id="logs", wrap=True, highlight=False, markup=False, max_lines=2000)
        yield Static("Ready", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        self.theme = "textual-dark"
        self.query_one("#feature-table", DataTable).add_columns("DID", "Field", "State", "Value")
        self.query_one("#raw-table", DataTable).add_columns("DID", "Read", "Bytes", "Raw hex / error")
        self.query_one("#diff-table", DataTable).add_columns("DID", "Before", "After")
        self.query_one("#dictionary-table", DataTable).add_columns("DID", "Name", "ECU", "Knowledge", "Access")
        self.query_one("#device-table", DataTable).add_columns("Name", "Address / UUID", "RSSI", "Services")
        self.query_one("#snapshot-table", DataTable).add_columns("ECU", "Mode", "Coverage", "Saved snapshot")
        self._show_dictionary()
        self._refresh_snapshots()
        self._set_busy(False)
        self.set_interval(0.25, self._tail_trace)
        snapshots = self.workbench.store.files("snapshots")
        if snapshots:
            self.query_one("#snapshot", Input).value = str(snapshots[-1])
        if self.workbench.link and self.workbench.link.kind == "demo":
            self.query_one("#entry", Input).value = "demo.display"
        if self.workbench.link and self.workbench.link.kind == "ble":
            self.query_one("#device-address", Input).value = getattr(self.workbench.link, "device", "")
        self._ui_ready = True

    def _input(self, key: str) -> str:
        return self.query_one(f"#{key}", Input).value.strip()

    def _path(self, key: str) -> Path:
        # Path("") is ".", which surfaced as "Is a directory" instead of naming the empty field.
        if not (value := self._input(key)):
            raise WorkbenchError(f"Empty field: {self.query_one(f'#{key}', Input).placeholder}")
        return Path(value).expanduser()

    def _number(self, key: str) -> int | None:
        value = self._input(key)
        try:
            return int(value, 0) if value else None
        except ValueError as exc:
            raise WorkbenchError(f"Not a number: {value!r} ({self.query_one(f'#{key}', Input).placeholder})") from exc

    def _status(self, text: str):
        self.query_one("#status", Static).update(text)
        self.query_one("#logs", RichLog).write(Text(text))

    def _set_busy(self, value: bool):
        self.busy = value
        for control in self.query("Button, Input, Select, Checkbox, DataTable"):
            control.disabled = value
        if not value and self.workbench.link is None:
            for key in ("capture", "apply", "reconcile"):
                self.query_one(f"#{key}", Button).disabled = True

    def on_button_pressed(self, event: Button.Pressed):
        self._start_operation(str(event.button.id), str(event.button.label))

    def _start_operation(self, action: str, label: str | None = None):
        if self.busy:
            self.notify("An operation is running. Wait for its result.")
            return
        self._set_busy(True)  # Set before dispatch; rapid clicks cannot start a second worker.
        self.last_error = None
        self._status(f"Working: {label or action}")
        self.run_worker(self._execute(action), exclusive=False, group="diagnostic",
                        name=action, exit_on_error=False)

    async def _execute(self, action: str):
        try:
            wb = self.workbench
            if action == "scan":
                self._devices = []
                self._show_devices()
                self.query_one("#device-count", Static).update("Scanning Bluetooth…")
                self._devices = await scan_devices()
                self._show_devices()
                self._status(f"Found {len(self._devices)} devices. Select an adapter by its exact address.")
            elif action in ("use-device", "simulator", "offline"):
                if action == "use-device":
                    address = self._input("device-address")
                    if not address:
                        raise WorkbenchError("Select a device row or enter its UUID/address or unique name")
                    link = BLELink(address)
                elif action == "simulator":
                    link = DemoLink(wb.store.root / "demo-state.json")
                else:
                    link = None
                if wb.link is not None:
                    await wb.link.close()
                wb.link = link
                self._invalidate_plan("Source changed. Capture or open a snapshot, then prepare a new plan.")
                self.transaction_path = None
                self.query_one("#transaction-file", Input).value = ""
                self.query_one("#entry", Input).value = "demo.display" if link and link.kind == "demo" else ""
                self.query_one("#target-did", Input).value = ""
                self.query_one("#mode-banner", Static).update(self.mode)
                self.query_one("#device-note", Static).update(self.mode)
                self._status(self.mode)
                if link is not None:
                    self.action_view("capture-tab")
            elif action == "capture":
                text = self._input("capture-dids")
                dids = [hex_did(item) for item in text.split(",") if item.strip()] or None
                async with wb:
                    path = await wb.capture(str(self.query_one("#ecu", Select).value), dids=dids,
                                            include_defaults=not self.query_one("#only-dids", Checkbox).value,
                                            include_dtc=self.query_one("#with-dtc", Checkbox).value)
                self._load_snapshot(path)
                self.action_view("explore-tab")
                document = wb.store.read(path)
                if document.get("complete"):
                    self.query_one("#capture-note", Static).update(f"Capture finished: {path}")
                    self._status(f"Saved snapshot: {path}")
                else:
                    self.query_one("#capture-note", Static).update(f"PARTIAL capture: {document.get('stopped_reason', 'Interrupted')}\n{path}")
                    self._status(f"PARTIAL capture saved; inspect the errors: {path}")
            elif action in ("inspect", "latest"):
                if action == "latest":
                    files = wb.store.files("snapshots")
                    if not files:
                        raise WorkbenchError("No saved snapshots yet")
                    path = files[-1]
                else:
                    path = self._path("snapshot")
                self._load_snapshot(path)
                self.action_view("explore-tab")
                self._status(f"Opened {path}")
            elif action == "diff":
                if self.snapshot_path is None:
                    raise WorkbenchError("Open a first snapshot")
                result = wb.diff(self.snapshot_path, self._path("diff-path"))
                table = self.query_one("#diff-table", DataTable)
                table.clear()
                for row in result.get("rows", []):
                    table.add_row(str(row.get("did", "")), str(row.get("before_hex", "")), str(row.get("after_hex", "")))
                self._status(result.get("warning") or f"Compared {len(result.get('rows', []))} differing records")
            elif action == "preview":
                if not self._input("snapshot"):
                    raise WorkbenchError("Capture or open a snapshot first")
                entry, did = self._input("entry"), self._input("target-did")
                if bool(entry) == bool(did):
                    raise WorkbenchError("Choose exactly one dictionary entry OR raw DID")
                full_hex = self._input("edit-hex")
                path = wb.make_plan(self._path("snapshot"), entry_id=entry or None, did=hex_did(did) if did else None,
                                    value_hex=full_hex or None, byte=self._number("edit-byte"),
                                    bit=self._number("edit-bit"), value=self._number("edit-value"))
                self._load_plan(path)
                self._status(f"Plan saved; no vehicle write: {path}")
            elif action == "load-plan":
                self._load_plan(self._path("plan-file"))
                self._status("Loaded plan; review before applying")
            elif action == "apply":
                path = self._path("plan-file")
                confirmation = self._input("confirmation")
                enabled = self.query_one("#enable-writes", Checkbox).value
                try:
                    async with wb:
                        receipt = await wb.apply(path, confirmation=confirmation, enable_writes=enabled)
                    self.transaction_path = receipt
                    self.query_one("#transaction-file", Input).value = str(receipt)
                    transaction = wb.store.read(receipt)
                    self._status(f"WRITE RESULT: {transaction.get('status', 'unknown')} · {receipt}")
                    self.query_one("#logs", RichLog).write(Text(json.dumps(transaction, indent=2, ensure_ascii=False)))
                finally:
                    self.query_one("#enable-writes", Checkbox).value = False
                    self.query_one("#confirmation", Input).value = ""
            elif action == "restore":
                path = wb.make_restore(self._path("transaction-file"))
                self._load_plan(path)
                self._status(f"Restore preview prepared. Review and confirm it: {path}")
            elif action == "reconcile":
                async with wb:
                    path = await wb.reconcile(self._path("transaction-file"))
                result = wb.store.read(path)
                self.transaction_path = path
                self.query_one("#transaction-file", Input).value = str(path)
                self._status(f"Reconciliation: {result.get('status')} · {path}")
            elif action == "export":
                path = wb.store.export(wb.dictionary.document())
                self._status(f"Local support ZIP saved: {path}")
            elif action == "dictionary":
                self._show_dictionary()
                self._status("Showing the loaded dictionary; restart with --dictionary to load a different file")
            elif action == "use-entry":
                if self._selected_entry is None:
                    raise WorkbenchError("Select a dictionary row first")
                self._select_entry(self._selected_entry)
                self.action_view("change-tab")
        except Exception as exc:  # noqa: BLE001 - keep UI alive to display evidence after any operation failure.
            self.last_error = str(exc)
            if action == "scan":
                self.query_one("#device-count", Static).update(f"Scan failed: {exc}")
            self._status(f"ERROR: {exc}")
            self.notify(str(exc), severity="error", timeout=10)
        finally:
            self._tail_trace()
            self._set_busy(False)

    def _load_snapshot(self, path: Path):
        snapshot = Store.read(path)
        if snapshot.get("schema") != "audi-workbench.snapshot/v1":
            raise WorkbenchError("This is not a snapshot file")
        self._invalidate_plan("Snapshot changed. Prepare a new plan from these values.")
        self._snapshot = snapshot
        self.snapshot_path = path
        self.query_one("#snapshot", Input).value = str(path)
        identity = snapshot.get("identity", {})
        display = {}
        for key, value in identity.items():
            try:
                display[key] = bytes.fromhex(value).decode("ascii", errors="replace").rstrip("\x00 ")
            except (TypeError, ValueError):
                display[key] = "Unreadable identity"
        self.query_one("#snapshot-note", Static).update(
            f"{snapshot.get('origin', {}).get('kind', '?').upper()} · ECU {snapshot.get('ecu', {}).get('key', '?')}\n"
            f"Part: {display.get('part_number', '?')} · SW: {display.get('sw_version', '?')}\n"
            f"ASAM: {display.get('asam_name', '?')} · VIN: {display.get('vin', '?')}\n{path}")
        self._show_records()
        self._refresh_snapshots()

    def _show_records(self):
        if self._snapshot is None:
            return
        snapshot = self._snapshot
        query = self._input("record-filter").casefold()
        decoded = self.workbench.dictionary.decode(snapshot)
        rows = [row for row in decoded if self._matches_query(query, row)]
        matched_dids = {row["did"] for row in rows}
        table = self.query_one("#feature-table", DataTable)
        table.clear()
        for row in rows:
            table.add_row(str(row.get("did", "")), Text(str(row.get("field") or row.get("name", ""))),
                          str(row.get("status", "unknown")), Text(str(row.get("value", ""))))
        table = self.query_one("#raw-table", DataTable)
        table.clear()
        for did, record in snapshot.get("records", {}).items():
            if query and did not in matched_dids and not self._matches_query(query, {"did": did, **record}):
                continue
            raw = record.get("hex", "")
            table.add_row(did, "OK" if record.get("ok") else "FAILED", str(len(raw) // 2),
                          Text(raw if record.get("ok") else str(record.get("error", "Unavailable"))), key=did)
        self.query_one("#record-count", Static).update(
            f"{table.row_count} of {len(snapshot.get('records', {}))} records · {len(rows)} decoded field rows")

    def _load_plan(self, path: Path):
        plan = Store.read(path)
        if plan.get("schema") != "audi-workbench.plan/v1":
            raise WorkbenchError("This is not a change-plan file")
        self.plan_path = path
        self.query_one("#plan-file", Input).value = str(path)
        self.query_one("#confirmation", Input).value = ""
        self.query_one("#enable-writes", Checkbox).value = False
        lines = [(f"{'ELIGIBLE FOR PREFLIGHT' if plan.get('eligible') else 'BLOCKED PREVIEW'} · "
                  f"{plan.get('entry_id')} · DID {plan.get('did')}"),
                 f"Before: {plan.get('before_hex')}", f"After:  {plan.get('after_hex')}"]
        for row in plan.get("changes", []):
            lines.append(f"Byte {row.get('offset')}: {row.get('before')} → {row.get('after')} · bits {row.get('bits')}")
        lines.extend(f"Blocked: {item}" for item in plan.get("blocked_reasons", []))
        lines.append(f"Confirmation: {plan.get('confirmation')}")
        self.query_one("#plan-preview", Static).update("\n".join(lines))
        self._plan_context = self._edit_context()

    def _edit_context(self) -> tuple:
        return tuple(self._input(key) for key in self.EDIT_CONTEXT)

    def _invalidate_plan(self, message: str = "Context changed. Prepare or load a plan again.", *, clear_path=True):
        self.plan_path = None
        self._plan_context = None
        if clear_path:
            self.query_one("#plan-file", Input).value = ""
        self.query_one("#confirmation", Input).value = ""
        self.query_one("#enable-writes", Checkbox).value = False
        self.query_one("#plan-preview", Static).update(message)

    @staticmethod
    def _matches_query(query: str, value: dict) -> bool:
        return not query or query in json.dumps(value, ensure_ascii=False).casefold()

    def _show_devices(self):
        query = self._input("device-filter").casefold()
        table = self.query_one("#device-table", DataTable)
        table.clear()
        for device in self._devices:
            if self._matches_query(query, device):
                table.add_row(_device_cell(device.get("name") or "Unnamed device"),
                              _device_cell(device["address"]), _device_cell(device.get("rssi", "?")),
                              _device_cell(", ".join(device.get("service_uuids", []))),
                              key=device["address"])
        self.query_one("#device-count", Static).update(
            f"{table.row_count} of {len(self._devices)} devices" if self._devices else
            "No devices found. Check Bluetooth permission and adapter power, then scan again.")

    def _refresh_snapshots(self):
        self._saved_snapshots = []
        for path in reversed(self.workbench.store.files("snapshots")):
            try:
                snapshot = Store.read(path)
                if (snapshot.get("schema") != "audi-workbench.snapshot/v1"
                        or any(not isinstance(snapshot.get(key), dict) for key in ("ecu", "origin", "records"))
                        or any(not isinstance(record, dict) for record in snapshot["records"].values())):
                    raise ValueError("Invalid snapshot metadata")
            except (OSError, ValueError, WorkbenchError):
                snapshot = {}
            ecu = snapshot.get("ecu", {}).get("key", "?")
            mode = snapshot.get("origin", {}).get("kind", "invalid")
            records = snapshot.get("records", {})
            coverage = f"{sum(bool(record.get('ok')) for record in records.values())}/{len(records)}"
            self._saved_snapshots.append((path, ecu, mode, coverage))
        self._show_snapshots()

    def _show_snapshots(self):
        query = self._input("snapshot-filter").casefold()
        table = self.query_one("#snapshot-table", DataTable)
        table.clear()
        for path, ecu, mode, coverage in self._saved_snapshots:
            if self._matches_query(query, {"file": path.name, "ecu": ecu, "mode": mode}):
                table.add_row(ecu, mode, coverage, path.name, key=str(path))
        self.query_one("#snapshot-count", Static).update(
            f"{table.row_count} of {len(self._saved_snapshots)} saved snapshots · newest first")

    def _show_dictionary(self):
        query = self._input("dictionary-filter").casefold()
        ecu = self.query_one("#dictionary-ecu", Select).value
        self._selected_entry = None
        self.query_one("#dictionary-detail", Static).update("Select a definition to see fields and evidence.")
        table = self.query_one("#dictionary-table", DataTable)
        table.clear()
        for entry in self.workbench.dictionary.entries:
            if ecu != "*" and entry["ecu"] not in ("*", ecu):
                continue
            if self._matches_query(query, entry):
                permission = "read-only"
                if entry.get("writable"):
                    permission = "simulator only" if entry.get("origin") == "demo" else "reviewed write"
                table.add_row(entry["did"], Text(entry["name"]), entry["ecu"], entry["confidence"],
                              permission, key=entry["id"])
        self.query_one("#dictionary-count", Static).update(
            f"{table.row_count} of {len(self.workbench.dictionary.entries)} definitions · Enter selects")

    def _show_entry(self, entry_id: str):
        entry = self.workbench.dictionary.entry(entry_id)
        fields = entry.get("fields", [])
        lines = [f"{entry['name']} · {entry['id']}",
                 f"DID {entry['did']} · ECU {entry['ecu']} · {entry.get('category', 'Uncategorized')}",
                 str(entry.get("description", "No additional description.")),
                 f"Evidence: {entry['confidence']} · origin: {entry.get('origin') or 'vehicle and simulator'}",
                 f"Default read: {'yes' if entry.get('read_by_default', True) else 'no; request explicitly'}",
                 f"Write: {'requires exact matching definition and preflight' if entry.get('writable') else 'read-only'}"]
        for field in fields:
            values = ", ".join(f"{key}={value}" for key, value in field.get("values", {}).items())
            lines.append(f"{field['name']}: byte {field['offset']}, mask 0x{field['mask']:02X} · {values}")
        if entry.get("applies_to"):
            lines.append("Exact identity required: " + ", ".join(entry["applies_to"]))
        lines.extend("Source: " + source for source in entry["source"])
        self.query_one("#dictionary-detail", Static).update("\n".join(lines))

    def _select_entry(self, entry_id: str):
        self._selected_entry = entry_id
        self._invalidate_plan()
        self.query_one("#entry", Input).value = entry_id
        self.query_one("#target-did", Input).value = ""
        for key in ("edit-byte", "edit-bit", "edit-value", "edit-hex"):
            self.query_one(f"#{key}", Input).value = ""
        self._show_entry(entry_id)
        self._status(f"Selected {entry_id}. Use in changes to prepare an offline preview.")

    def on_input_changed(self, event: Input.Changed):
        if not self._ui_ready:
            return
        if (event.input.id in self.EDIT_CONTEXT and self.plan_path is not None
                and self._edit_context() != self._plan_context):
            self._invalidate_plan(clear_path=event.input.id != "plan-file")
        if self.busy:
            return
        handlers = {"device-filter": self._show_devices, "snapshot-filter": self._show_snapshots,
                    "record-filter": self._show_records, "dictionary-filter": self._show_dictionary}
        if handler := handlers.get(event.input.id):
            handler()

    def on_select_changed(self, event: Select.Changed):
        if self._ui_ready and not self.busy and event.select.id == "dictionary-ecu":
            self._show_dictionary()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if self._ui_ready and not self.busy and event.data_table.id == "dictionary-table":
            self._show_entry(str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if self.busy:
            return
        key = str(event.row_key.value)
        if event.data_table.id == "device-table":
            self.query_one("#device-address", Input).value = key
            self._start_operation("use-device", "Use device")
        elif event.data_table.id == "snapshot-table":
            self.query_one("#snapshot", Input).value = key
            self._start_operation("inspect", "Open snapshot")
        elif event.data_table.id == "dictionary-table":
            self._select_entry(key)
        elif event.data_table.id == "raw-table":
            self._invalidate_plan()
            self.query_one("#target-did", Input).value = key
            self.query_one("#entry", Input).value = ""
            self.action_view("change-tab")
            self._status("Raw DID selected for offline preview. A raw selection does not permit writing.")

    def _tail_trace(self):
        path = self.workbench.store.trace_path
        if path is None or not self.is_running:
            return
        if path != self._trace_seen:
            self._trace_seen, self._trace_offset = path, 0
        try:
            with path.open("r", encoding="utf-8") as stream:
                stream.seek(self._trace_offset)
                lines = stream.readlines(64 * 1024)
                self._trace_offset = stream.tell()
            log = self.screen_stack[0].query_one("#logs", RichLog)
            for line in lines:
                log.write(Text(line.rstrip()))
        except OSError:
            pass  # The durable store, not this viewer, owns evidence persistence.

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("view", "focus_search", "clear_search", "show_help"):
            return not self.busy and len(self.screen_stack) == 1
        return True

    def action_view(self, tab: str):
        self.set_focus(None)
        self.query_one("#tabs", TabbedContent).active = tab
        focus = {"devices-tab": "device-table", "capture-tab": "ecu", "explore-tab": "raw-table",
                 "change-tab": "entry", "dictionary-tab": "dictionary-table", "evidence-tab": "export"}
        self.query_one(f"#{focus[tab]}").focus()

    def action_focus_search(self):
        tab = self.query_one("#tabs", TabbedContent).active
        if target := self.SEARCH_TARGETS.get(tab):
            self.query_one(f"#{target[0]}", Input).focus()
        else:
            self.notify("Search is available in Devices, Capture, Explore and Dictionary.")

    def action_clear_search(self):
        tab = self.query_one("#tabs", TabbedContent).active
        if target := self.SEARCH_TARGETS.get(tab):
            self.query_one(f"#{target[0]}", Input).value = ""
            self.query_one(f"#{target[1]}", DataTable).focus()

    def action_show_help(self):
        self.push_screen(HelpScreen())

    def action_safe_quit(self):
        if self.busy:
            self.notify("Wait for the active operation; quitting could interrupt a write.", severity="warning")
        else:
            self.exit()

    async def action_quit(self):
        self.action_safe_quit()


def run_tui(workbench):
    WorkbenchApp(workbench).run()
