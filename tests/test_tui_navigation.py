import asyncio
import json

import pytest
from textual.widgets import Button, Checkbox, DataTable, Input, Static, TabbedContent

from audi_workbench import tui
from audi_workbench.dictionary import Dictionary
from audi_workbench.model import TransportError
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store


async def finish(app, pilot):
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
    await pilot.pause()


async def click(pilot, selector):
    pilot.app.query_one(selector).scroll_visible(animate=False, immediate=True)
    await pilot.pause()
    assert await pilot.click(selector)


async def captured_workbench(tmp_path):
    wb = Workbench(Store(tmp_path), Dictionary.load(), DemoLink(tmp_path / "demo-state.json"))
    async with wb:
        snapshot = await wb.capture()
    return wb, snapshot


async def type_input(pilot, selector, value):
    control = pilot.app.query_one(selector, Input)
    control.scroll_visible(animate=False, immediate=True)
    control.focus()
    await pilot.pause()
    await pilot.press("ctrl+shift+a", *value)
    await pilot.pause()
    assert control.value == value


async def load_armed_plan(app, pilot, plan):
    await pilot.press("f4")
    app.query_one("#plan-file", Input).value = str(plan)
    await click(pilot, "#load-plan")
    await finish(app, pilot)
    assert app.last_error is None
    assert app.plan_path == plan
    confirmation = Store.read(plan)["confirmation"]
    app.query_one("#confirmation", Input).value = confirmation
    app.query_one("#enable-writes", Checkbox).value = True
    await pilot.pause()
    assert app.plan_path == plan
    assert app.query_one("#confirmation", Input).value == confirmation
    assert app.query_one("#enable-writes", Checkbox).value
    preview = app.query_one("#plan-preview", Static).content
    assert "After:  00050000" in preview
    return preview


async def test_scan_filter_and_select_exact_device_without_connecting(tmp_path, monkeypatch):
    scans = []

    async def scan(timeout=8):
        scans.append(timeout)
        return [
            {"name": "IOS-Vlink", "address": "uuid-one", "rssi": -30, "service_uuids": ["18f0"]},
            {"name": "IOS-Vlink", "address": "uuid-two", "rssi": -40, "service_uuids": ["18f0"]},
        ]

    monkeypatch.setattr(tui, "scan_devices", scan, raising=False)
    wb, snapshot = await captured_workbench(tmp_path)
    plan = wb.make_plan(snapshot, entry_id="demo.display", byte=0, bit=0, value=0)
    wb.link = None
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#tabs", TabbedContent).active == "devices-tab"
        app._load_plan(plan)
        app.query_one("#confirmation", Input).value = Store.read(plan)["confirmation"]
        app.query_one("#enable-writes", Checkbox).value = True
        await click(pilot, "#scan")
        await finish(app, pilot)
        assert app.query_one("#device-table", DataTable).row_count == 2
        app.query_one("#device-filter", Input).value = "uuid-two"
        await pilot.pause()
        table = app.query_one("#device-table", DataTable)
        assert table.row_count == 1
        table.focus()
        await pilot.press("enter")
        assert app.query_one("#device-address", Input).value == "uuid-two"
        await click(pilot, "#use-device")
        await finish(app, pilot)
        assert len(scans) == 1
        assert wb.link.kind == "ble"
        assert wb.link.device == "uuid-two"
        assert not wb.link._ready
        assert "selected" in app.mode.lower()
        assert "uuid-two" in app.mode
        assert app.plan_path is None
        assert app.query_one("#plan-file", Input).value == ""
        assert app.query_one("#confirmation", Input).value == ""
        assert not app.query_one("#enable-writes", Checkbox).value
        assert not app.query_one("#capture", Button).disabled


@pytest.mark.parametrize(("name", "display_name"), [
    ("VGATE\x1b]0;AUDIT\x1b\\", "VGATE\\x1b]0;AUDIT\\x1b\\"),
    ("[red]Å\x1b[31m\x00\x1f\x7f\x9b\x9f", r"[red]Å\x1b[31m\x00\x1f\x7f\x9b\x9f"),
], ids=["osc", "csi-and-control-codes"])
async def test_scan_escapes_terminal_controls_and_preserves_exact_selection(
        tmp_path, monkeypatch, name, display_name):
    address = "01234567-89ab-cdef-0123-456789abcdef"
    device = {"name": name, "address": address, "rssi": -30, "service_uuids": ["18f0"]}

    async def scan(timeout=8):
        return [device]

    monkeypatch.setattr(tui, "scan_devices", scan)
    wb = Workbench(Store(tmp_path), Dictionary.load())
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(120, 40)) as pilot:
        await click(pilot, "#scan")
        await finish(app, pilot)
        table = app.query_one("#device-table", DataTable)
        rendered = app.screen._compositor.render_full_update().render_segments(app.console)
        assert name not in rendered
        assert "\x1b]0;AUDIT" not in rendered
        assert "\x1b[31m" not in rendered
        assert "\x9b" not in rendered
        assert display_name in rendered
        assert table.get_row(address)[0].plain == display_name
        assert app._devices == [
            {"name": name, "address": address, "rssi": -30, "service_uuids": ["18f0"]},
        ]
        table.focus()
        await pilot.press("enter")
        assert app.query_one("#device-address", Input).value == address
        await click(pilot, "#use-device")
        await finish(app, pilot)
        assert wb.link.device == address
        assert not wb.link._ready


@pytest.mark.parametrize("failure", [False, True], ids=["empty", "permission-error"])
async def test_scan_empty_or_error_returns_to_usable_offline_state(tmp_path, monkeypatch, failure):
    async def scan(timeout=8):
        if failure:
            raise TransportError("Bluetooth permission denied")
        return []

    monkeypatch.setattr(tui, "scan_devices", scan, raising=False)
    app = tui.WorkbenchApp(Workbench(Store(tmp_path), Dictionary.load()))
    async with app.run_test(size=(80, 30)) as pilot:
        await click(pilot, "#scan")
        await finish(app, pilot)
        assert app.query_one("#device-table", DataTable).row_count == 0
        assert not app.busy
        assert app.workbench.link is None
        assert not app.query_one("#scan", Button).disabled
        assert app.query_one("#capture", Button).disabled
        assert app.last_error == ("Bluetooth permission denied" if failure else None)


async def test_active_scan_ignores_duplicate_scan_and_source_switch(tmp_path, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def scan(timeout=8):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return []

    monkeypatch.setattr(tui, "scan_devices", scan, raising=False)
    app = tui.WorkbenchApp(Workbench(Store(tmp_path), Dictionary.load()))
    async with app.run_test(size=(100, 32)) as pilot:
        try:
            await click(pilot, "#scan")
            await asyncio.wait_for(started.wait(), timeout=5)
            assert app.busy
            for button in ("scan", "simulator", "offline", "use-device"):
                assert app.post_message(Button.Pressed(app.query_one(f"#{button}", Button)))
            await pilot.pause()
            assert app.workbench.link is None
            assert calls == 1
            assert len(list(app.workers)) == 1
            assert all(widget.disabled for widget in app.query("Button, Input, Select, Checkbox, DataTable"))
            release.set()
            await finish(app, pilot)
            assert not app.busy
            assert app.workbench.link is None
        finally:
            release.set()


async def test_dictionary_search_selection_and_keyboard_navigation(tmp_path):
    wb, snapshot = await captured_workbench(tmp_path)
    plan = wb.make_plan(snapshot, entry_id="demo.display", byte=0, bit=0, value=0)
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(120, 40)) as pilot:
        app._load_snapshot(snapshot)
        app._load_plan(plan)
        app.query_one("#confirmation", Input).value = Store.read(plan)["confirmation"]
        app.query_one("#enable-writes", Checkbox).value = True
        await pilot.press("f5")
        assert app.query_one("#tabs", TabbedContent).active == "dictionary-tab"
        table = app.query_one("#dictionary-table", DataTable)
        table.focus()
        await pilot.press("/")
        search = app.query_one("#dictionary-filter", Input)
        assert app.focused is search
        search.value = "vag.coding"
        await pilot.pause()
        assert table.row_count == 1
        table.focus()
        await pilot.press("enter")
        assert app.query_one("#entry", Input).value == "vag.coding"
        assert app.query_one("#target-did", Input).value == ""
        assert app.plan_path is None
        assert not app.query_one("#enable-writes", Checkbox).value
        assert app.query_one("#confirmation", Input).value == ""
        search.value = "no matching definition"
        await pilot.pause()
        await click(pilot, "#use-entry")
        await finish(app, pilot)
        assert app.last_error == "Select a dictionary row first"
        assert app.query_one("#tabs", TabbedContent).active == "dictionary-tab"
        search.value = "vag.coding"
        await pilot.pause()
        table.focus()
        await pilot.press("enter")
        await click(pilot, "#use-entry")
        await finish(app, pilot)
        assert app.query_one("#tabs", TabbedContent).active == "change-tab"
        entry = app.query_one("#entry", Input)
        entry.value = ""
        entry.focus()
        await pilot.press("/", "?", "q")
        assert entry.value == "/?q"
        assert app.is_running
        assert len(app.screen_stack) == 1
        await pilot.press("f1")
        assert app.query_one("#tabs", TabbedContent).active == "devices-tab"


async def test_snapshot_picker_and_record_search_preserve_selected_record(tmp_path):
    wb, snapshot = await captured_workbench(tmp_path)
    plan = wb.make_plan(snapshot, entry_id="demo.display", byte=0, bit=0, value=0)
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(120, 40)) as pilot:
        app._load_plan(plan)
        app.query_one("#confirmation", Input).value = Store.read(plan)["confirmation"]
        app.query_one("#enable-writes", Checkbox).value = True
        app.query_one("#snapshot-filter", Input).value = snapshot.name
        await pilot.pause()
        picker = app.query_one("#snapshot-table", DataTable)
        assert picker.row_count == 1
        picker.focus()
        await pilot.press("enter")
        await finish(app, pilot)
        assert app.snapshot_path == snapshot
        assert app.plan_path is None
        assert not app.query_one("#enable-writes", Checkbox).value
        assert app.query_one("#confirmation", Input).value == ""
        assert app.query_one("#tabs", TabbedContent).active == "explore-tab"
        app.query_one("#record-filter", Input).value = "D100"
        await pilot.pause()
        records = app.query_one("#raw-table", DataTable)
        assert records.row_count == 1
        assert app.query_one("#feature-table", DataTable).row_count == 2
        records.focus()
        await pilot.press("enter")
        assert app.query_one("#tabs", TabbedContent).active == "change-tab"
        assert app.query_one("#target-did", Input).value == "D100"
        assert app.query_one("#entry", Input).value == ""


@pytest.mark.parametrize(("field", "value"), [
    ("snapshot", "new-snapshot.json"),
    ("entry", "demo.other"),
    ("target-did", "D101"),
    ("edit-byte", "1"),
    ("edit-bit", "1"),
    ("edit-value", "1"),
    ("edit-hex", "01050001"),
    ("plan-file", "new-plan.json"),
])
async def test_manual_edit_invalidates_armed_plan(tmp_path, field, value):
    wb, snapshot = await captured_workbench(tmp_path)
    plan = wb.make_plan(snapshot, entry_id="demo.display", byte=0, bit=0, value=0)
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(120, 40)) as pilot:
        await load_armed_plan(app, pilot, plan)
        await pilot.press("f2" if field == "snapshot" else "f4")
        await type_input(pilot, f"#{field}", value)
        assert app.plan_path is None
        assert app.query_one("#confirmation", Input).value == ""
        assert not app.query_one("#enable-writes", Checkbox).value
        assert app.query_one("#plan-preview", Static).content == (
            "Context changed. Prepare or load a plan again.")
        assert app.query_one("#plan-file", Input).value == (value if field == "plan-file" else "")
        assert wb.link.writes == []
        assert wb.store.files("transactions") == []


async def test_search_and_confirmation_typing_preserve_loaded_plan(tmp_path):
    wb, snapshot = await captured_workbench(tmp_path)
    plan = wb.make_plan(snapshot, entry_id="demo.display", byte=0, bit=0, value=0)
    confirmation = Store.read(plan)["confirmation"]
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(120, 40)) as pilot:
        app._load_snapshot(snapshot)
        await pilot.pause()
        preview = await load_armed_plan(app, pilot, plan)
        for tab, field, value in (
            ("f1", "device-filter", "adapter"),
            ("f2", "snapshot-filter", "demo"),
            ("f3", "record-filter", "D100"),
            ("f5", "dictionary-filter", "display"),
            ("f4", "confirmation", "reviewing"),
        ):
            await pilot.press(tab)
            await type_input(pilot, f"#{field}", value)
            assert app.plan_path == plan
            assert app.query_one("#plan-file", Input).value == str(plan)
            assert app.query_one("#confirmation", Input).value == (
                value if field == "confirmation" else confirmation)
            assert app.query_one("#enable-writes", Checkbox).value
            assert app.query_one("#plan-preview", Static).content == preview
        assert wb.link.writes == []
        assert wb.store.files("transactions") == []


async def test_trace_callback_does_not_consume_evidence_after_shutdown(tmp_path):
    wb, _ = await captured_workbench(tmp_path)
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
    offset = app._trace_offset
    wb.store.trace({"event": "synthetic_after_shutdown"})
    app._tail_trace()
    assert app._trace_offset == offset


async def test_saved_snapshot_browser_recovers_from_invalid_artifacts(tmp_path):
    wb, snapshot = await captured_workbench(tmp_path)
    corrupt = wb.store.root / "snapshots" / "corrupt.json"
    document = Store.read(snapshot)
    document["sha256"] = "0" * 64
    corrupt.write_text(json.dumps(document), encoding="utf-8")
    unreadable = wb.store.root / "snapshots" / "unreadable.json"
    # A directory with an artifact suffix gives a portable read error, even when running as root.
    unreadable.mkdir()
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(120, 40)) as pilot:
        picker = app.query_one("#snapshot-table", DataTable)
        assert app.is_running
        assert not app.busy
        assert picker.row_count == 3
        assert picker.get_row(str(snapshot))[1] == "demo"
        for path in (corrupt, unreadable):
            assert picker.get_row(str(path))[1] == "invalid"

        async def open_saved(path):
            await pilot.press("f2")
            app.query_one("#snapshot-filter", Input).value = path.name
            await pilot.pause()
            assert picker.row_count == 1
            picker.focus()
            await pilot.press("enter")
            await finish(app, pilot)

        await open_saved(snapshot)
        assert app.last_error is None
        assert app.snapshot_path == snapshot
        assert app.query_one("#tabs", TabbedContent).active == "explore-tab"
        assert app.query_one("#raw-table", DataTable).row_count > 0
        for path in (corrupt, unreadable):
            await open_saved(path)
            assert app.last_error
            assert path.name in app.last_error
            assert "ERROR:" in app.query_one("#status", Static).content
            assert not app.busy
            assert not picker.disabled
            assert not app.query_one("#snapshot-filter", Input).disabled
            assert not app.query_one("#inspect", Button).disabled
            assert app.snapshot_path == snapshot

        await open_saved(snapshot)
        assert app.last_error is None
        assert app.query_one("#tabs", TabbedContent).active == "explore-tab"
        assert app.query_one("#raw-table", DataTable).row_count > 0
        assert wb.link.writes == []
        assert wb.store.files("transactions") == []


async def test_help_and_escape_are_available_without_trapping_navigation(tmp_path):
    app = tui.WorkbenchApp(Workbench(Store(tmp_path), Dictionary.load()))
    async with app.run_test(size=(80, 30)) as pilot:
        app.query_one("#device-table", DataTable).focus()
        await pilot.press("?")
        assert len(app.screen_stack) == 2
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
        await pilot.press("/")
        search = app.query_one("#device-filter", Input)
        assert app.focused is search
        search.value = "anything"
        await pilot.press("escape")
        assert search.value == ""
        assert app.focused is app.query_one("#device-table", DataTable)
