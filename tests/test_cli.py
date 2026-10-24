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
