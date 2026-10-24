from copy import deepcopy

import pytest

from audi_workbench.dictionary import Dictionary
from audi_workbench.model import ECUS, SafetyError, WorkbenchError
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store

IDENTITY_DIDS = {0xF190, 0xF187, 0xF189, 0xF191, 0xF1A3, 0xF197, 0xF19E, 0xF1A2}
CATALOG_ENTRIES = {
    "vag.serial_number": "F18C",
    "vag.fazit_id": "F17C",
    "vag.active_session": "F186",
    "vag.workshop_system_name": "F1AA",
    "vag.bootloader_id": "F1F4",
    "vag.software_block_version": "F1AB",
    "vag.production_change": "F17E",
    "vag.programming_information": "F1DF",
}


def test_opt_in_read_metadata_excludes_only_the_marked_record():
    vin = Dictionary.load().entry("vag.vin")
    vin.pop("read_by_default", None)  # Older dictionaries retain their existing behavior.
    optional = deepcopy(vin)
    optional.update(id="custom.serial", did="F18C", read_by_default=False)
    dictionary = Dictionary([vin, optional])

    assert dictionary.read_dids("5F", "ble") == [0xF190]
    optional["read_by_default"] = True
    assert Dictionary([vin, optional]).read_dids("5F", "ble") == [0xF18C, 0xF190]


@pytest.mark.parametrize("key,value", [
    ("read_by_default", "false"), ("read_by_default", 0),
    ("read_by_default", 1), ("read_by_default", None),
    ("category", ""), ("category", "  "), ("category", []), ("category", 5),
    ("description", ""), ("description", "\t"), ("description", {}), ("description", None),
])
def test_malformed_catalog_metadata_is_rejected(key, value):
    entry = Dictionary.load().entry("vag.vin")
    entry[key] = value
    with pytest.raises(WorkbenchError, match=key):
        Dictionary([entry])


@pytest.mark.parametrize("ecu", ["01", "19", "5F"])
def test_catalog_expansion_preserves_existing_automatic_reads(ecu):
    dictionary = Dictionary.load()
    expected = IDENTITY_DIDS | ({0x0600, 0x22AD} if ecu == "5F" else set())
    assert set(dictionary.read_dids(ecu, "ble")) == expected
    assert set(dictionary.read_dids(ecu, "demo")) == expected | ({0xD100} if ecu == "5F" else set())


@pytest.mark.parametrize("entry_id,did", CATALOG_ENTRIES.items())
def test_catalog_records_are_optional_read_only_and_cannot_authorize_writes(entry_id, did):
    dictionary = Dictionary.load()
    entry = dictionary.entry(entry_id)
    assert entry["did"] == did
    assert entry["read_by_default"] is False
    assert entry["writable"] is False
    assert entry["confidence"] == "source-confirmed"
    assert entry["category"] and entry["description"]
    assert int(did, 16) not in dictionary.read_dids("5F", "ble")
    with pytest.raises(SafetyError, match="read.only"):
        dictionary.check_write(entry, {}, ECUS["5F"], "ble", b"\x00", b"\x01")


async def test_explicit_catalog_read_decodes_text_without_changing_default_capture(tmp_path):
    store = Store(tmp_path)
    dictionary = Dictionary.load()
    link = DemoLink()
    raw_serial = b"SAMPLE-SERIAL  \x00".hex().upper()
    link.state["ecus"]["5F"]["records"]["F18C"] = raw_serial
    async with Workbench(store, dictionary, link) as workbench:
        default_path = await workbench.capture()
        assert "F18C" not in store.read(default_path)["records"]
        explicit_path = await workbench.capture(dids=[0xF18C])

    snapshot = store.read(explicit_path)
    assert snapshot["records"]["F18C"]["hex"] == raw_serial
    serial, = [row for row in dictionary.decode(snapshot) if row["did"] == "F18C"]
    assert serial["entry_id"] == "vag.serial_number"
    assert serial["status"] == "decoded"
    assert serial["value"] == "SAMPLE-SERIAL"
    assert serial["raw"] == raw_serial
    assert serial["writable"] is False
    assert not link.writes


def test_named_raw_session_data_does_not_invent_a_session_decoder():
    dictionary = Dictionary.load()
    snapshot = {"origin": {"kind": "ble"}, "ecu": ECUS["5F"].as_dict(), "identity": {},
                "records": {"F186": {"ok": True, "hex": "03"}}}
    session, = dictionary.decode(snapshot)
    assert session["entry_id"] == "vag.active_session"
    assert session["status"] == "raw"
    assert session["raw"] == "03"
    assert session["writable"] is False
