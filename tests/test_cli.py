import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("options,kind", [([], None), (["--demo"], "demo"),
                                        (["--device", "adapter-uuid"], "ble")])
def test_no_command_launches_tui_with_selected_mode(tmp_path, monkeypatch, options, kind):
    from audi_workbench.cli import main

    opened = []
    monkeypatch.setattr("audi_workbench.tui.run_tui", opened.append)
    assert main(["--data-dir", str(tmp_path), *options]) == 0
    workbench, = opened
    assert (workbench.link.kind if workbench.link is not None else None) == kind
    assert workbench.store.root == tmp_path
    assert not workbench.store.files("snapshots")
    assert not workbench.store.files("transactions")


def invoke(tmp_path, *arguments):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "audi_workbench", "--data-dir", str(tmp_path),
                           *arguments], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False)


def test_help_explains_hardware_boundary(tmp_path):
    result = invoke(tmp_path, "--help")
    assert result.returncode == 0, result.stderr
    assert "not hardware-tested" in " ".join(result.stdout.split())
    assert "capture" in result.stdout


def test_real_capture_requires_explicit_device(tmp_path):
    result = invoke(tmp_path, "capture")
    assert result.returncode != 0
    assert "--device" in result.stderr


def test_demo_capture_and_dictionary_have_machine_readable_results(tmp_path):
    result = invoke(tmp_path, "--demo", "capture")
    assert result.returncode == 0, result.stderr
    capture = json.loads(result.stdout)
    assert capture["artifact"]
    result = invoke(tmp_path, "inspect", capture["artifact"])
    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown["snapshot"]["origin"]["kind"] == "demo"
    assert any(row["did"] == "D100" for row in shown["decoded"])


def test_demo_write_restore_end_to_end(tmp_path):
    capture = invoke(tmp_path, "--demo", "capture")
    assert capture.returncode == 0, capture.stderr
    snapshot = json.loads(capture.stdout)["artifact"]
    result = invoke(tmp_path, "--demo", "plan", snapshot, "--entry", "demo.display",
                    "--byte", "0", "--bit", "0", "--value", "0")
    assert result.returncode == 0, result.stderr
    planned = json.loads(result.stdout)
    plan = planned["document"]
    assert plan["before_hex"] == "01050000"
    assert plan["after_hex"] == "00050000"
    denied = invoke(tmp_path, "--demo", "apply", planned["artifact"], "--confirm", plan["confirmation"])
    assert denied.returncode != 0
    applied = invoke(tmp_path, "--demo", "apply", planned["artifact"], "--enable-writes",
                     "--confirm", plan["confirmation"])
    assert applied.returncode == 0, applied.stderr
    receipt = json.loads(applied.stdout)
    assert receipt["document"]["status"] == "verified"
    restored = invoke(tmp_path, "--demo", "restore", receipt["artifact"])
    assert restored.returncode == 0, restored.stderr
    restore = json.loads(restored.stdout)
    assert restore["document"]["after_hex"] == "01050000"
    applied = invoke(tmp_path, "--demo", "apply", restore["artifact"], "--enable-writes",
                     "--confirm", restore["document"]["confirmation"])
    assert applied.returncode == 0, applied.stderr


def test_collect_exports_three_independent_snapshots(tmp_path):
    result = invoke(tmp_path, "--demo", "collect")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["complete"]
    assert [item["ecu"] for item in data["captures"]] == ["5F", "19", "01"]
    assert not data["uploaded"]
    with zipfile.ZipFile(data["artifact"]) as archive:
        assert len([name for name in archive.namelist() if name.startswith("snapshots/")]) == 3
        assert "dictionary.json" in archive.namelist()
    first = json.loads(Path(data["captures"][0]["artifact"]).read_text())
    assert "F18C" in first["requested_dids"]  # Opt-in identification catalog is read on collect.


def test_explicit_did_file_and_read_only_template(tmp_path):
    dids = tmp_path / "dids.txt"
    dids.write_text("D100\n0600 # raw coding\n")
    result = invoke(tmp_path, "--demo", "capture", "--only", "--dids-file", str(dids))
    assert result.returncode == 0, result.stderr
    capture = json.loads(result.stdout)
    assert capture["document"]["records"]["D100"]["hex"] == "01050000"
    output = tmp_path / "candidate.json"
    result = invoke(tmp_path, "dictionary-template", capture["artifact"], "--output", str(output))
    assert result.returncode == 0, result.stderr
    candidate = json.loads(output.read_text())
    additions = [entry for entry in candidate["entries"] if entry["id"].startswith("candidate.")]
    assert additions
    assert all(not entry.get("writable") for entry in additions)


def test_list_reports_newest_artifacts_for_agents(tmp_path):
    first = json.loads(invoke(tmp_path, "--demo", "capture").stdout)
    plan = json.loads(invoke(tmp_path, "plan", first["artifact"], "--entry", "demo.display",
                             "--byte", "0", "--bit", "0", "--value", "0").stdout)
    (tmp_path / "snapshots" / "broken.json").write_text("{}")
    result = invoke(tmp_path, "list")
    assert result.returncode == 0, result.stderr
    listing = json.loads(result.stdout)
    snapshots = {item["artifact"]: item for item in listing["snapshots"]}
    assert snapshots[first["artifact"]]["complete"] is True
    assert snapshots[first["artifact"]]["successful"] > 0
    assert "error" in snapshots[str(tmp_path / "snapshots" / "broken.json")]
    assert listing["plans"][0]["artifact"] == plan["artifact"]
    assert listing["plans"][0]["eligible"] is True


def test_real_vehicle_apply_refuses_a_non_interactive_caller(tmp_path, monkeypatch, capsys):
    import io

    from audi_workbench.cli import main

    monkeypatch.setattr("sys.stdin", io.StringIO())
    monkeypatch.setattr("audi_workbench.transport.BLELink.open",
                        lambda self: pytest.fail("must not connect"))
    code = main(["--data-dir", str(tmp_path), "--device", "IOS-Vlink", "apply", "plan.json",
                 "--enable-writes", "--confirm", "WRITE 5F D100 X"])
    assert code == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_raw_reads_in_one_session_and_keeps_negative_answers(tmp_path):
    result = invoke(tmp_path, "raw", "--demo", "--ecu", "5F", "22F190", "22BEEF", "1902FF")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    vin, missing, dtc = data["results"]
    assert vin["ok"] and vin["response"].startswith("62F190")
    assert bytes.fromhex(vin["data"]).startswith(b"DEMO")
    assert not missing["ok"] and missing["nrc"] == "31"
    assert dtc["ok"] and dtc["data"].startswith("02")
    assert data["complete"]


@pytest.mark.parametrize("request_hex", ["2ED10000", "3101FF00", "1101", "14FFFFFF", "2701", "1002",
                                         "2F0600", "3E00", "22F1", "19"])
def test_raw_refuses_everything_but_reads_before_connecting(tmp_path, request_hex):
    result = invoke(tmp_path, "--demo", "raw", request_hex)
    assert result.returncode == 2
    assert "Error:" in result.stderr
    assert not (tmp_path / "demo-state.json").exists()  # The simulator was never opened.


def test_raw_service_guard_cannot_be_bypassed_from_python(tmp_path):
    import asyncio

    from audi_workbench.dictionary import Dictionary
    from audi_workbench.model import SafetyError
    from audi_workbench.service import Workbench
    from audi_workbench.simulator import DemoLink
    from audi_workbench.store import Store

    link = DemoLink(tmp_path / "demo-state.json")

    async def run():
        async with Workbench(Store(tmp_path), Dictionary.load(), link) as wb:
            with pytest.raises(SafetyError):
                await wb.raw("5F", [bytes.fromhex("22F190"), bytes.fromhex("2ED10000")])
    asyncio.run(run())
    assert link.requests == [] and link.writes == []


def test_global_options_work_after_the_command(tmp_path):
    result = invoke(tmp_path, "capture", "--demo", "--ecu", "19")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["document"]["ecu"]["key"] == "19"
    clash = invoke(tmp_path, "--device", "x", "list", "--demo")
    assert clash.returncode == 2 and "mutually exclusive" in clash.stderr
