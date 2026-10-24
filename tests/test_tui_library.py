"""Research browsing stays offline and cannot change a prepared write plan."""
from copy import deepcopy

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Checkbox, DataTable, Input, Select, Static, TabbedContent

from audi_workbench import tui
from audi_workbench.dictionary import Dictionary
from audi_workbench.library import CodingLibrary
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store


@pytest.fixture
def research_library(monkeypatch):
    source = {"id": "synthetic.reference", "title": "Synthetic research source",
              "url": "https://example.invalid/synthetic-research", "kind": "test fixture", "access": "excerpt"}
    base = {
        "kind": "adaptation", "ecu": "5F", "families": ["MHS2"], "platforms": ["Synthetic test platform"],
        "summary": "A synthetic candidate for exercising research browsing; not a vehicle recipe.",
        "locator": {"channel": "Synthetic channel", "did": None, "byte": None, "bit": None, "mask": None},
        "reported_values": ["Example value from synthetic evidence"],
        "prerequisites": ["Collect exact ECU identity before evaluating applicability."],
        "limitations": ["No physical vehicle validation; fixture evidence only."],
        "verification_questions": ["Does the exact channel exist on this ECU?"],
        "evidence": [{"source_id": source["id"], "claim": "Synthetic claim for UI verification.",
                      "locator_hint": "Fixture paragraph one"}], "writable": False,
    }
    entries = [
        {**deepcopy(base), "id": "synthetic.display_documented", "name": "Synthetic display documented",
         "category": "Display", "status": "documented"},
        {**deepcopy(base), "id": "synthetic.display_reported", "name": "Synthetic display reported",
         "category": "Display", "status": "reported"},
        {**deepcopy(base), "id": "synthetic.sound_conflicting", "name": "Synthetic sound conflicting",
         "category": "Sound", "status": "conflicting"},
        {**deepcopy(base), "id": "synthetic.background", "name": "Synthetic background topic",
         "category": "Background", "status": "out_of_scope", "families": ["MH2P"]},
    ]
    library = CodingLibrary({"schema": "audi-workbench.library/v1", "version": "synthetic-test",
                             "scope": "Synthetic UI tests only", "index_base": 0,
                             "sources": [source], "entries": entries})
    monkeypatch.setattr(tui.CodingLibrary, "load", classmethod(lambda cls, path=None: library))
    return library


def cursor_key(table):
    return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value


@pytest.mark.parametrize("size", [(120, 40), (80, 30), (60, 24)])
async def test_library_keyboard_search_details_and_all_view_shortcuts(tmp_path, research_library, size):
    wb = Workbench(Store(tmp_path), Dictionary.load())
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=size) as pilot:
        for key, tab, target in (
            ("f1", "devices-tab", "device-table"), ("f2", "capture-tab", "ecu"),
            ("f3", "explore-tab", "raw-table"), ("f4", "change-tab", "entry"),
            ("f5", "dictionary-tab", "dictionary-table"), ("f6", "evidence-tab", "export"),
            ("f7", "library-tab", "library-table"),
        ):
            await pilot.press(key)
            assert app.query_one("#tabs", TabbedContent).active == tab
            assert app.focused is app.query_one(f"#{target}")

        table = app.query_one("#library-table", DataTable)
        assert table.row_count == 4
        await pilot.press("/")
        search = app.query_one("#library-filter", Input)
        assert app.focused is search
        await pilot.press("/", "?", "q")
        assert search.value == "/?q"
        assert len(app.screen_stack) == 1
        search.value = "display_reported"
        await pilot.pause()
        assert table.row_count == 1
        await pilot.press("enter")
        assert app.focused is table
        assert cursor_key(table) == "synthetic.display_reported"
        await pilot.press("tab")
        panel = app.query_one("#library-detail-scroll", VerticalScroll)
        assert app.focused is panel
        await pilot.press("shift+tab")
        assert app.focused is table
        await pilot.press("enter")
        assert app.focused is panel
        await pilot.press("pagedown")
        await pilot.pause()
        assert panel.scroll_y > 0
        await pilot.press("escape")
        assert search.value == ""
        assert app.focused is table
        assert cursor_key(table) == "synthetic.display_reported"
        assert wb.link is None
        assert not list(app.workers)


async def test_library_combines_filters_and_exposes_claims_without_stale_details(tmp_path, research_library):
    research_library.sources["synthetic.reference"].update({
        "family_evidence": "Synthetic MHS2 source; not a physical vehicle report.",
        "identity_evidence": {"software_train": "MHS2_SYNTHETIC", "part_number": None},
        "independence_group": "synthetic-shared-lineage", "notes": "The complete source was not available.",
    })
    app = tui.WorkbenchApp(Workbench(Store(tmp_path), Dictionary.load()))
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("f7")
        table = app.query_one("#library-table", DataTable)
        app.query_one("#library-category", Select).value = "Display"
        app.query_one("#library-status", Select).value = "reported"
        app.query_one("#library-filter", Input).value = "synthetic-research"
        await pilot.pause()
        assert table.row_count == 1
        assert cursor_key(table) == "synthetic.display_reported"
        detail = app.query_one("#library-detail", Static).content
        for text in ("Synthetic display reported", "DID: unknown", "Byte: unknown", "Bit: unknown",
                     "Vehicle state: not established", "Prerequisites:", "Limitations:",
                     "Does the exact channel exist on this ECU?", "Synthetic research source",
                     "https://example.invalid/synthetic-research", "Access: excerpt",
                     "Synthetic claim for UI verification.", "Fixture paragraph one",
                     "Family evidence: Synthetic MHS2 source", '"software_train": "MHS2_SYNTHETIC"',
                     "Independence group: synthetic-shared-lineage", "The complete source was not available."):
            assert text in detail

        app.query_one("#library-status", Select).value = "conflicting"
        await pilot.pause()
        assert table.row_count == 0
        assert not table.show_cursor
        assert app._selected_library is None
        assert "No matching research candidates" in app.query_one("#library-detail", Static).content
        assert "Synthetic display reported" not in app.query_one("#library-detail", Static).content
        table.focus()
        await pilot.press("enter")
        assert app.focused is table

        app.query_one("#library-category", Select).value = "*"
        await pilot.pause()
        assert table.row_count == 1
        assert table.show_cursor
        assert app._selected_library == "synthetic.sound_conflicting"
        assert "claims or variants differ" in app.query_one("#library-detail", Static).content


async def test_library_browsing_preserves_armed_plan_without_using_the_link(tmp_path, research_library, monkeypatch):
    link = DemoLink(tmp_path / "demo-state.json")
    wb = Workbench(Store(tmp_path), Dictionary.load(), link)
    async with wb:
        snapshot = await wb.capture()
    plan = wb.make_plan(snapshot, entry_id="demo.display", byte=0, bit=0, value=0)
    confirmation = Store.read(plan)["confirmation"]

    async def unexpected_diagnostic_call(*args, **kwargs):
        raise AssertionError("Research browsing must not open or use a diagnostic link")

    monkeypatch.setattr(link, "open", unexpected_diagnostic_call)
    monkeypatch.setattr(link, "request", unexpected_diagnostic_call)
    app = tui.WorkbenchApp(wb)
    async with app.run_test(size=(100, 36)) as pilot:
        app._load_snapshot(snapshot)
        await pilot.pause()
        app._load_plan(plan)
        app.query_one("#confirmation", Input).value = confirmation
        app.query_one("#enable-writes", Checkbox).value = True
        await pilot.pause()
        preview = app.query_one("#plan-preview", Static).content
        edit_values = tuple(app.query_one(f"#{field}", Input).value for field in app.EDIT_CONTEXT)

        await pilot.press("f7", "/")
        app.query_one("#library-filter", Input).value = "display"
        app.query_one("#library-category", Select).value = "Display"
        await pilot.pause()
        await pilot.press("enter", "down", "enter", "pagedown", "escape", "f4")
        assert app.plan_path == plan
        assert app.query_one("#confirmation", Input).value == confirmation
        assert app.query_one("#enable-writes", Checkbox).value
        assert app.query_one("#plan-preview", Static).content == preview
        assert tuple(app.query_one(f"#{field}", Input).value for field in app.EDIT_CONTEXT) == edit_values
        assert app.last_error is None
        assert not list(app.workers)
        assert link.writes == []
        assert wb.store.files("transactions") == []


async def test_library_cursor_survives_filtering_and_clears_after_no_results(tmp_path, research_library):
    app = tui.WorkbenchApp(Workbench(Store(tmp_path), Dictionary.load()))
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("f7")
        table = app.query_one("#library-table", DataTable)
        table.move_cursor(row=table.get_row_index("synthetic.display_reported"))
        await pilot.pause()
        await pilot.press("/")
        search = app.query_one("#library-filter", Input)
        search.value = "display"
        await pilot.pause()
        assert table.row_count == 2
        assert cursor_key(table) == "synthetic.display_reported"
        assert app._selected_library == "synthetic.display_reported"
        search.value = "unmatched research query"
        await pilot.pause()
        assert table.row_count == 0
        assert app._selected_library is None
        search.value = "sound"
        await pilot.pause()
        assert table.row_count == 1
        assert cursor_key(table) == "synthetic.sound_conflicting"
        assert app._selected_library == "synthetic.sound_conflicting"


async def test_library_family_filter_separates_other_unit_recipes(tmp_path, research_library):
    app = tui.WorkbenchApp(Workbench(Store(tmp_path), Dictionary.load()), library=research_library)
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("f7")
        table = app.query_one("#library-table", DataTable)
        app.query_one("#library-family", Select).value = "MHS2"
        await pilot.pause()
        assert table.row_count == 3
        assert "synthetic.background" not in table.rows
        app.query_one("#library-family", Select).value = "MH2P"
        await pilot.pause()
        assert table.row_count == 1
        assert cursor_key(table) == "synthetic.background"
