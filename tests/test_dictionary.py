import json
from copy import deepcopy

import pytest

from audi_workbench.dictionary import Dictionary
from audi_workbench.model import ECUS, SafetyError, WorkbenchError
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store


def sample_snapshot(dictionary):
    entry = dictionary.entry("demo.display")
    return {
        "origin": {"kind": "demo"}, "ecu": ECUS["5F"].as_dict(),
        "identity": entry["applies_to"].copy(),
        "records": {"D100": {"ok": True, "hex": "01050000"},
                    "CAFE": {"ok": True, "hex": "00FF"}},
    }


def test_known_reads_do_not_probe_synthetic_records_on_real_car():
    dictionary = Dictionary.load()
    assert 0x0600 in dictionary.read_dids("5F", "ble")
    assert 0x22AD in dictionary.read_dids("5F", "ble")
    assert 0xD100 not in dictionary.read_dids("5F", "ble")
    assert 0xD100 in dictionary.read_dids("5F", "demo")


def test_decode_distinguishes_known_enabled_from_unknown_bytes():
    dictionary = Dictionary.load()
    rows = dictionary.decode(sample_snapshot(dictionary))
    assert any(row["status"] == "decoded" and row["value"] == "Enabled" for row in rows)
    assert any(row["did"] == "CAFE" and row["status"] == "unknown" and row["raw"] == "00FF" for row in rows)


def test_fingerprint_mismatch_never_decodes_boolean_as_disabled():
    dictionary = Dictionary.load()
    snapshot = sample_snapshot(dictionary)
    snapshot["identity"]["sw_version"] = "DEADBEEF"
    rows = dictionary.decode(snapshot)
    assert not any(row["status"] == "decoded" for row in rows if row["did"] == "D100")
    assert any(row["status"] == "unavailable" for row in rows if row["did"] == "D100")


def test_demo_write_cannot_cross_to_live_link():
    dictionary = Dictionary.load()
    entry = dictionary.entry("demo.display")
    with pytest.raises(SafetyError, match="origin"):
        dictionary.check_write(entry, entry["applies_to"], ECUS["5F"], "ble",
                               bytes.fromhex("01050000"), bytes.fromhex("00050000"))


@pytest.mark.parametrize("after", ["010500", "81050000", "010B0000"])
def test_write_rejects_length_mask_and_invalid_enum(after):
    dictionary = Dictionary.load()
    entry = dictionary.entry("demo.display")
    with pytest.raises(SafetyError):
        dictionary.check_write(entry, entry["applies_to"], ECUS["5F"], "demo",
                               bytes.fromhex("01050000"), bytes.fromhex(after))


@pytest.mark.parametrize("before", ["010B0000", "010F0000"])
def test_write_rejects_unknown_preimage_that_cannot_be_restored(before):
    dictionary = Dictionary.load()
    entry = dictionary.entry("demo.display")
    with pytest.raises(SafetyError, match="cannot be restored"):
        dictionary.check_write(entry, entry["applies_to"], ECUS["5F"], "demo",
                               bytes.fromhex(before), bytes.fromhex("010A0000"))


@pytest.mark.parametrize("before", ["010B0000", "010F0000"])
async def test_unknown_preimage_blocks_plan(tmp_path, before):
    store = Store(tmp_path)
    link = DemoLink()
    link.state["ecus"]["5F"]["records"]["D100"] = before
    async with Workbench(store, Dictionary.load(), link) as wb:
        snapshot = await wb.capture()
        path = wb.make_plan(snapshot, entry_id="demo.display", byte=1, value=10)
        plan = store.read(path)
        assert not plan["eligible"]
        assert any("cannot be restored" in reason for reason in plan["blocked_reasons"])
        assert not any(request[0] == 0x2E for _, request in link.requests)


@pytest.mark.parametrize("before", ["010B0000", "010F0000"])
async def test_apply_rechecks_unknown_preimage_even_if_plan_is_marked_eligible(tmp_path, before):
    store = Store(tmp_path)
    link = DemoLink()
    link.state["ecus"]["5F"]["records"]["D100"] = before
    async with Workbench(store, Dictionary.load(), link) as wb:
        snapshot = await wb.capture()
        path = wb.make_plan(snapshot, entry_id="demo.display", byte=1, value=10)
        plan = store.read(path)
        plan["eligible"] = True
        plan["blocked_reasons"] = []
        store.update(path, plan)
        with pytest.raises(SafetyError, match="cannot be restored"):
            await wb.apply(path, plan["confirmation"], True)
        assert not any(request[0] == 0x2E for _, request in link.requests)
        assert link.state["ecus"]["5F"]["records"]["D100"] == before


def test_exact_safe_demo_change_is_allowed():
    dictionary = Dictionary.load()
    entry = dictionary.entry("demo.display")
    dictionary.check_write(entry, entry["applies_to"], ECUS["5F"], "demo",
                           bytes.fromhex("01050000"), bytes.fromhex("00050000"))


def test_known_enum_change_and_reverse_are_allowed():
    dictionary = Dictionary.load()
    entry = dictionary.entry("demo.display")
    before, after = bytes.fromhex("01050000"), bytes.fromhex("010A0000")
    dictionary.check_write(entry, entry["applies_to"], ECUS["5F"], "demo", before, after)
    dictionary.check_write(entry, entry["applies_to"], ECUS["5F"], "demo", after, before)


def test_readable_entry_is_not_a_restore_permission():
    dictionary = Dictionary.load()
    entry = dictionary.entry("vag.coding")
    with pytest.raises(SafetyError, match="read.only"):
        dictionary.check_write(entry, {}, ECUS["5F"], "ble", b"\x00", b"\x01")


def test_untrusted_edited_entry_is_rechecked_at_write_time():
    dictionary = Dictionary.load()
    entry = deepcopy(dictionary.entry("demo.display"))
    del entry["applies_to"]["vin"]
    with pytest.raises(SafetyError):
        dictionary.check_write(entry, entry["applies_to"], ECUS["5F"], "demo",
                               bytes.fromhex("01050000"), bytes.fromhex("00050000"))


def test_duplicate_ids_are_rejected_on_load(tmp_path):
    entry = Dictionary.load().entry("vag.coding")
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps({"schema": "audi-workbench.dictionary/v1", "entries": [entry, entry]}))
    with pytest.raises(WorkbenchError, match="duplicate"):
        Dictionary.load(path)

