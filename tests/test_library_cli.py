import json
import stat

from audi_workbench.cli import main
from audi_workbench.library import CodingLibrary


def test_library_cli_is_offline_even_with_an_adapter_selected(tmp_path, monkeypatch, capsys):
    def no_link(*args):
        raise AssertionError("Research browsing must not construct a diagnostic link")
    monkeypatch.setattr("audi_workbench.cli._link", no_link)
    assert main(["library", "--query", "Apple_DIO", "--device", "DO-NOT-CONNECT",
                 "--data-dir", str(tmp_path / "unused")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["matched"] >= 1
    assert result["summary"]["writable"] == 0
    assert not (tmp_path / "unused").exists()


def test_packaged_library_cli_report_and_no_overwrite(tmp_path, capsys):
    output = tmp_path / "review.json"
    path = tmp_path / "map.csv"
    path.write_text("Channel,Value,Unit\nApple_DIO,OFF,\n")
    assert main(["library", "--map", str(path), "--output", str(output)]) == 0
    capsys.readouterr()
    report = json.loads(output.read_text())
    assert report["coverage"]["named_observed"] >= 1
    assert report["coverage"]["vehicle_verified"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    original = output.read_bytes()
    assert main(["library", "--output", str(output)]) == 2
    assert output.read_bytes() == original


def test_packaged_research_is_complete_as_a_review_input():
    library = CodingLibrary.load()
    assert library.entries and library.sources
    assert library.search("Apple_DIO", family="MHS2")
    assert library.search(status="conflicting")
    assert library.search(status="out_of_scope")
    assert library.summary()["vehicle_verified"] == 0
    assert all(entry["evidence"] and entry["verification_questions"] for entry in library.entries)


def test_custom_catalog_is_passed_to_the_same_tui(tmp_path, monkeypatch):
    document = CodingLibrary.load().document()
    document["entries"] = document["entries"][:1]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(document))
    opened = []
    monkeypatch.setattr("audi_workbench.tui.run_tui", lambda wb, library=None: opened.append((wb, library)))
    assert main(["tui", "--library", str(path), "--data-dir", str(tmp_path / "run")]) == 0
    assert opened[0][0].link is None
    assert len(opened[0][1].entries) == 1
