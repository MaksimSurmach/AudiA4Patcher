import asyncio

from textual.widgets import Button, Checkbox, Input, TabbedContent

from audi_workbench.dictionary import Dictionary
from audi_workbench.model import TransportError
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store
from audi_workbench.tui import WorkbenchApp


async def finish(app, pilot):
    await pilot.pause()
    for _ in range(200):
        if not app.busy:
            await pilot.pause()
            return
        await asyncio.sleep(0.01)
    raise AssertionError("TUI operation did not finish")


async def click(pilot, selector):
    pilot.app.query_one(selector).scroll_visible(animate=False, immediate=True)
    await pilot.pause()
    assert await pilot.click(selector), f"Click did not reach {selector}"


async def test_capture_preview_apply_restore_through_buttons(tmp_path):
    store = Store(tmp_path)
    link = DemoLink(tmp_path / "demo-state.json")
    app = WorkbenchApp(Workbench(store, Dictionary.load(), link))
    async with app.run_test(size=(120, 60)) as pilot:
        await click(pilot, "#capture")
        await finish(app, pilot)
        assert app.last_error is None
        assert app.snapshot_path is not None
        app.query_one("#tabs", TabbedContent).active = "change-tab"
        await pilot.pause()
        app.query_one("#edit-byte", Input).value = "0"
        app.query_one("#edit-bit", Input).value = "0"
        app.query_one("#edit-value", Input).value = "0"
        await click(pilot, "#preview")
        await finish(app, pilot)
        assert app.last_error is None
        assert app.plan_path is not None
        plan = store.read(app.plan_path)
        assert plan["after_hex"] == "00050000"
        assert not app.query_one("#enable-writes", Checkbox).value
        app.query_one("#confirmation", Input).value = plan["confirmation"]
        app.query_one("#enable-writes", Checkbox).value = True
        await click(pilot, "#apply")
        await finish(app, pilot)
        assert app.last_error is None
        assert app.transaction_path is not None
        assert store.read(app.transaction_path)["status"] == "verified"
        assert not app.query_one("#enable-writes", Checkbox).value
        assert app.query_one("#confirmation", Input).value == ""
        await click(pilot, "#restore")
        await finish(app, pilot)
        assert app.last_error is None
        restore = store.read(app.plan_path)
        assert restore["after_hex"] == "01050000"
        app.query_one("#confirmation", Input).value = restore["confirmation"]
        app.query_one("#enable-writes", Checkbox).value = True
        await click(pilot, "#apply")
        await finish(app, pilot)
        assert app.last_error is None
        assert store.read(app.transaction_path)["status"] == "verified"


async def test_offline_mode_blocks_connection_and_reports_file_error(tmp_path):
    app = WorkbenchApp(Workbench(Store(tmp_path), Dictionary.load()))
    async with app.run_test(size=(90, 30)) as pilot:
        assert app.query_one("#capture").disabled
        app.query_one("#tabs", TabbedContent).active = "capture-tab"
        app.query_one("#snapshot", Input).value = str(tmp_path / "missing.json")
        await click(pilot, "#inspect")
        await finish(app, pilot)
        assert app.last_error
        assert not app.busy


async def prepared_plan(tmp_path, link):
    store = Store(tmp_path)
    wb = Workbench(store, Dictionary.load(), link)
    async with wb:
        snapshot = await wb.capture()
        path = wb.make_plan(snapshot, entry_id="demo.display", byte=0, bit=0, value=0)
    return wb, path


async def load_apply_inputs(app, pilot, plan_path):
    app.query_one("#tabs", TabbedContent).active = "change-tab"
    app._load_plan(plan_path)
    plan = app.workbench.store.read(plan_path)
    app.query_one("#confirmation", Input).value = plan["confirmation"]
    app.query_one("#enable-writes", Checkbox).value = True
    await pilot.pause()


async def test_inflight_write_blocks_quit_and_duplicate_dispatch_until_receipt(tmp_path, monkeypatch):
    class HeldWriteLink(DemoLink):
        def __init__(self):
            super().__init__(tmp_path / "demo-state.json")
            self.write_started = asyncio.Event()
            self.release_write = asyncio.Event()
            self.write_cancelled = False

        async def request(self, ecu, payload, **kwargs):
            response = await super().request(ecu, payload, **kwargs)
            if payload[0] == 0x2E:
                self.write_started.set()
                try:
                    await self.release_write.wait()
                except asyncio.CancelledError:
                    self.write_cancelled = True
                    raise
            return response

    link = HeldWriteLink()
    wb, plan_path = await prepared_plan(tmp_path, link)
    app = WorkbenchApp(wb)
    exit_calls = []
    exit_app = app.exit

    def record_exit(*args, **kwargs):
        exit_calls.append(True)
        return exit_app(*args, **kwargs)

    monkeypatch.setattr(app, "exit", record_exit)
    async with app.run_test(size=(120, 60)) as pilot:
        await load_apply_inputs(app, pilot, plan_path)
        workers_started = []
        run_worker = app.run_worker

        def record_worker(*args, **kwargs):
            worker = run_worker(*args, **kwargs)
            workers_started.append(worker)
            return worker

        monkeypatch.setattr(app, "run_worker", record_worker)
        try:
            await click(pilot, "#apply")
            await asyncio.wait_for(link.write_started.wait(), timeout=5)
            worker, = workers_started
            receipt, = wb.store.files("transactions")
            assert wb.store.read(receipt)["status"] == "attempting"
            assert app.busy
            assert all(control.disabled for control in app.query("Button, Input, Select, Checkbox"))

            # Exercise actual key dispatch and the command-palette quit action.
            await asyncio.wait_for(pilot.press("ctrl+c", "ctrl+q", "q"), timeout=5)
            await app.action_quit()
            # A disabled button ignores clicks; an already-queued press must also be rejected.
            await pilot.click("#apply")
            assert app.post_message(Button.Pressed(app.query_one("#apply", Button)))
            assert app.post_message(Button.Pressed(app.query_one("#simulator", Button)))
            assert app.post_message(Button.Pressed(app.query_one("#offline", Button)))
            await pilot.pause()
            assert not exit_calls
            assert app.is_running
            assert app.busy
            assert workers_started == [worker]
            assert worker.is_running
            assert not worker.is_cancelled
            assert not link.write_cancelled
            assert len(link.writes) == 1
            assert wb.link is link
            assert wb.store.read(receipt)["status"] == "attempting"
            assert all(control.disabled for control in app.query("Button, Input, Select, Checkbox"))

            link.release_write.set()
            await asyncio.wait_for(worker.wait(), timeout=5)
            await pilot.pause()
            assert app.transaction_path == receipt
            assert wb.store.read(receipt)["status"] == "verified"
            assert app.last_error is None
            assert not app.busy
            assert not worker.is_cancelled
            assert not link.write_cancelled
            assert len(link.writes) == 1
            assert all(not control.disabled for control in app.query("Button, Input, Select, Checkbox"))
            assert not app.query_one("#enable-writes", Checkbox).value
            assert app.query_one("#confirmation", Input).value == ""
            await app.action_quit()
            assert len(exit_calls) == 1
        finally:
            link.release_write.set()


async def test_failed_apply_reenables_controls_and_clears_write_confirmation(tmp_path):
    class HeldPreflightFailureLink(DemoLink):
        def __init__(self):
            super().__init__(tmp_path / "demo-state.json")
            self.preflight_started = asyncio.Event()
            self.release_preflight = asyncio.Event()
            self.fail_preflight = False

        async def voltage(self):
            if not self.fail_preflight:
                return await super().voltage()
            self.preflight_started.set()
            await self.release_preflight.wait()
            raise TransportError("Voltage measurement failed during preflight")

    link = HeldPreflightFailureLink()
    wb, plan_path = await prepared_plan(tmp_path, link)
    link.fail_preflight = True
    app = WorkbenchApp(wb)
    async with app.run_test(size=(120, 60)) as pilot:
        await load_apply_inputs(app, pilot, plan_path)
        try:
            await click(pilot, "#apply")
            await asyncio.wait_for(link.preflight_started.wait(), timeout=5)
            worker, = list(app.workers)
            assert app.busy
            assert all(control.disabled for control in app.query("Button, Input, Select, Checkbox"))
            link.release_preflight.set()
            await asyncio.wait_for(worker.wait(), timeout=5)
            await pilot.pause()
            assert app.last_error == "Voltage measurement failed during preflight"
            assert not app.busy
            assert app.is_running
            assert all(not control.disabled for control in app.query("Button, Input, Select, Checkbox"))
            assert not app.query_one("#enable-writes", Checkbox).value
            assert app.query_one("#confirmation", Input).value == ""
            assert not link.writes
            assert not wb.store.files("transactions")
            assert not link.is_open
            with Store(wb.store.root).lock():
                pass
        finally:
            link.release_preflight.set()


async def test_empty_paths_and_bad_numbers_name_the_field(tmp_path):
    app = WorkbenchApp(Workbench(Store(tmp_path), Dictionary.load(), DemoLink(tmp_path / "demo-state.json")))
    async with app.run_test(size=(120, 40)) as pilot:
        for action in ("apply", "load-plan", "restore", "reconcile", "inspect"):
            app.query_one("#snapshot", Input).value = ""
            app._start_operation(action)
            await finish(app, pilot)
            assert app.last_error.startswith("Empty field:"), (action, app.last_error)
        app._start_operation("capture")
        await finish(app, pilot)
        app.query_one("#edit-byte", Input).value = "x"
        app.query_one("#edit-value", Input).value = "1"
        app._start_operation("preview")
        await finish(app, pilot)
        assert app.last_error.startswith("Not a number: 'x'")
        app.query_one("#capture-dids", Input).value = "zz"
        app._start_operation("capture")
        await finish(app, pilot)
        assert "DID must be hexadecimal" in app.last_error
