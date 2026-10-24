import asyncio
import os

from textual.widgets import Input

from audi_workbench.dictionary import Dictionary
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store
from audi_workbench.tui import WorkbenchApp

STAMP_NS = 1_700_000_000_000_000_000


def same_second_paths(store):
    # The random suffix is deliberately in the opposite order from the writes.
    return (store.root / "snapshots" / "20261024T080000-ffffffffffff.json",
            store.root / "snapshots" / "20261024T080000-000000000000.json")


def set_mtime(path, nanoseconds):
    os.utime(path, ns=(nanoseconds, nanoseconds))


def test_files_order_same_second_snapshots_by_actual_write_time(tmp_path):
    store = Store(tmp_path)
    older, newer = same_second_paths(store)
    store.update(older, {"value": "older"})
    store.update(newer, {"value": "newer"})
    set_mtime(older, STAMP_NS + 100)
    set_mtime(newer, STAMP_NS + 200)
    (older.parent / "notes.txt").write_text("Not a snapshot")

    assert store.files("snapshots") == [older, newer]


def test_updated_artifact_becomes_latest(tmp_path):
    store = Store(tmp_path)
    older, newer = same_second_paths(store)
    store.update(older, {"value": "initial"})
    store.update(newer, {"value": "newer"})
    set_mtime(older, STAMP_NS + 100)
    set_mtime(newer, STAMP_NS + 200)
    store.update(older, {"value": "updated"})
    set_mtime(older, STAMP_NS + 300)

    assert store.files("snapshots") == [newer, older]
    assert store.read(store.files("snapshots")[-1])["value"] == "updated"


def test_identical_write_times_have_deterministic_filename_order(tmp_path):
    store = Store(tmp_path)
    older, newer = same_second_paths(store)
    for path in (older, newer):
        store.update(path, {"value": path.name})
        set_mtime(path, STAMP_NS)

    assert store.files("snapshots") == sorted([older, newer])


async def test_tui_latest_opens_newest_same_second_snapshot(tmp_path):
    store = Store(tmp_path)
    link = DemoLink()
    workbench = Workbench(store, Dictionary.load(), link)
    older, newer = same_second_paths(store)
    async with workbench:
        (await workbench.capture()).rename(older)
        link.state["ecus"]["5F"]["records"]["D100"] = "00050000"
        (await workbench.capture()).rename(newer)
    set_mtime(older, STAMP_NS + 100)
    set_mtime(newer, STAMP_NS + 200)

    app = WorkbenchApp(workbench)
    async with app.run_test(size=(120, 60)) as pilot:
        assert app.query_one("#snapshot", Input).value == str(newer)
        # An existing field selection must not override the explicit Latest action.
        app.query_one("#snapshot", Input).value = str(older)
        assert await pilot.click("#latest")
        await pilot.pause()
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
        assert app.last_error is None
        assert app.snapshot_path == newer
        assert app.query_one("#snapshot", Input).value == str(newer)
        assert store.read(app.snapshot_path)["records"]["D100"]["hex"] == "00050000"
