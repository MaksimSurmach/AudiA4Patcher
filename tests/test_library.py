"""Research evidence must remain separate from verified vehicle semantics."""
import hashlib
from copy import deepcopy

import pytest

from audi_workbench.dictionary import Dictionary
from audi_workbench.library import SCHEMA, CodingLibrary, read_adaptation_map
from audi_workbench.model import WorkbenchError


@pytest.fixture
def document():
    common = {
        "category": "Connectivity", "kind": "adaptation", "ecu": "5F", "families": ["MHS2"],
        "platforms": ["MLB evo"], "status": "reported", "summary": "Synthetic research claim.",
        "reported_values": ["ON"], "prerequisites": ["Exact dataset review"],
        "limitations": ["Synthetic fixture, never an actual vehicle definition."],
        "verification_questions": ["Does the exact channel exist on this unit?"], "writable": False,
        "evidence": [{"source_id": "test.source", "claim": "Synthetic claim.", "locator_hint": "Fixture"}],
    }
    return {
        "schema": SCHEMA, "version": "test", "scope": "Synthetic tests", "index_base": 0,
        "sources": [{"id": "test.source", "url": "https://example.org/synthetic", "title": "Test evidence",
                     "kind": "synthetic", "access": "full"}],
        "entries": [
            {**deepcopy(common), "id": "test.apple", "name": "Apple DIO candidate",
             "locator": {"channel": "Apple_DIO", "did": None, "byte": None, "bit": None, "mask": None}},
            {**deepcopy(common), "id": "test.byte", "name": "Conflicting USB byte", "kind": "coding",
             "status": "conflicting",
             "locator": {"channel": None, "did": "0600", "byte": 1, "bit": 7, "mask": 192}},
            {**deepcopy(common), "id": "test.other", "name": "Other family recipe", "families": ["MH2P"],
             "status": "out_of_scope",
             "locator": {"channel": "Other_feature", "did": None, "byte": None, "bit": None, "mask": None}},
        ],
    }


def snapshot(*, family="EV_MUHigScl6C3DELP", origin="ble", ecu="5F"):
    return {"schema": "audi-workbench.snapshot/v1", "origin": {"kind": origin}, "ecu": {"key": ecu},
            "identity": {"asam_name": family.encode().hex(), "part_number": b"SYNTHETIC".hex()},
            "records": {"0600": {"ok": True, "hex": "00C4"}}}


def test_search_and_source_detail(document):
    library = CodingLibrary(document)
    assert [entry["id"] for entry in library.search("apple dio", family="mhs2")] == ["test.apple"]
    assert [entry["id"] for entry in library.search(status="conflicting")] == ["test.byte"]
    assert len(library.search("example.org")) == 3
    assert library.search(family="MHI2") == []
    assert library.search("reported_values") == []
    assert library.detail("test.apple")["sources"][0]["id"] == "test.source"
    with pytest.raises(WorkbenchError, match="Unknown research"):
        library.entry("missing")


@pytest.mark.parametrize("change", [
    lambda doc: doc["entries"][0].update(writable=True),
    lambda doc: doc["entries"][0].update(status="verified"),
    lambda doc: doc["entries"][0]["evidence"][0].update(source_id="missing"),
    lambda doc: doc["entries"][0]["locator"].update(did="IDE03471"),
    lambda doc: doc["entries"][1]["locator"].update(byte=-1),
    lambda doc: doc["entries"][1]["locator"].update(bit=8),
    lambda doc: doc["sources"][0].update(url="file:///tmp/private"),
])
def test_invalid_library_does_not_load(document, change):
    change(document)
    with pytest.raises(WorkbenchError, match="Invalid research library"):
        CodingLibrary(document)


def test_observed_bit_never_becomes_verified_feature_or_write(document):
    report = CodingLibrary(document).assess(snapshot=snapshot())
    items = {item["entry_id"]: item for item in report["items"]}
    assert report["detected_family"] == "MHS2"
    assert items["test.byte"]["compatibility"] == "family_candidate"
    assert items["test.byte"]["raw_observation"]["bit_value"] == 1
    assert items["test.byte"]["raw_observation"]["masked_hex"] == "C0"
    assert items["test.other"]["compatibility"] == "family_mismatch"
    assert all(item["feature_state"] == "unverified" and not item["writable"] for item in report["items"])
    assert report["coverage"]["vehicle_verified"] == report["coverage"]["writable"] == 0
    assert "vin" not in report["snapshot_identity"]
    with pytest.raises(WorkbenchError):
        Dictionary(document["entries"])


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({"origin": "demo"}, "simulator"), ({"ecu": "19"}, "ecu_mismatch"),
    ({"family": "EV_MUStd4CDELP"}, "family_unknown"),
])
def test_other_units_and_demo_are_not_mhs2_evidence(document, kwargs, expected):
    report = CodingLibrary(document).assess(snapshot=snapshot(**kwargs))
    assert all(item["compatibility"] == expected for item in report["items"])


def test_short_or_failed_record_remains_unavailable(document):
    doc = snapshot()
    doc["records"]["0600"]["hex"] = "00"
    result = CodingLibrary(document).assess(snapshot=doc)
    assert result["items"][1]["raw_observation"]["state"] == "payload_too_short"
    doc["records"]["0600"] = {"ok": False, "error": "NRC 31"}
    result = CodingLibrary(document).assess(snapshot=doc)
    assert result["items"][1]["raw_observation"] == {"state": "read_failed", "error": "NRC 31"}


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_csv_preserves_raw_named_values_and_ambiguity(document, tmp_path, delimiter):
    path = tmp_path / "map.csv"
    path.write_text(";Local header must not be copied\n" + delimiter.join(("Channel", "Value", "Unit")) + "\n"
                    + delimiter.join(("IDE03471-ENG130249-Vehicle configuration-Apple_DIO", "OFF", "")) + "\n",
                    encoding="utf-8-sig")
    adaptation_map = read_adaptation_map(path)
    report = CodingLibrary(document).assess(adaptation_map=adaptation_map)
    item = report["items"][0]
    assert item["adaptation_observation"]["state"] == "label_observed"
    assert item["adaptation_observation"]["matches"][0]["value"] == "OFF"
    assert item["feature_state"] == "unverified"
    assert report["map_identity_verified"] is False
    adaptation_map["records"].append(deepcopy(adaptation_map["records"][0]))
    report = CodingLibrary(document).assess(adaptation_map=adaptation_map)
    assert report["items"][0]["adaptation_observation"]["state"] == "ambiguous"


def test_original_vcds_rows_and_exact_identifier_matching(document, tmp_path):
    document["entries"][0]["locator"]["channel"] = "IDE03471-ENG130249-Apple_DIO"
    path = tmp_path / "vcds.csv"
    path.write_bytes(b';SW: synthetic only\r\nIDE03471-ENG130249-Vehicle configuration-Apple_DIO,ON ,1\r\n'
                     b'IDE03471-ENG999999-Vehicle configuration-Apple_DIO,OFF ,1\r\n')
    report = CodingLibrary(document).assess(adaptation_map=read_adaptation_map(path))
    named = report["items"][0]["adaptation_observation"]
    assert named["method"] == "identifier-label" and named["state"] == "label_observed"
    assert named["matches"][0]["value"] == "ON "
    assert named["matches"][0]["unit"] == ""
    assert named["matches"][0]["extra_fields"] == ["1"]


def test_unrecognised_csv_is_an_error(tmp_path):
    path = tmp_path / "unrelated.csv"
    path.write_text("price,quantity\n10,2\n")
    with pytest.raises(WorkbenchError, match="No adaptation rows"):
        read_adaptation_map(path)


def test_different_explicit_identifiers_do_not_match_through_same_label(document, tmp_path):
    document["entries"][0]["locator"]["channel"] = "IDE03471-ENG130249-Vehicle configuration-Apple_DIO"
    path = tmp_path / "different.csv"
    path.write_text("IDE03471-ENG999999-Vehicle configuration-Apple_DIO,ON,\n")
    report = CodingLibrary(document).assess(adaptation_map=read_adaptation_map(path))
    assert report["items"][0]["adaptation_observation"]["state"] == "not_found"


def test_oversized_csv_field_is_a_user_facing_error(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text("Channel,Value,Unit\nApple_DIO," + "x" * 200000 + ",\n")
    with pytest.raises(WorkbenchError, match="Malformed CSV"):
        read_adaptation_map(path)


def test_malformed_snapshot_metadata_is_a_user_facing_error(document):
    doc = snapshot()
    doc["identity"] = []
    with pytest.raises(WorkbenchError, match="malformed identity"):
        CodingLibrary(document).assess(snapshot=doc)


@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_explicit_csv_header_preserves_decimal_comma_values(document, tmp_path, delimiter):
    channel = "IDE00001-ENG00002-Synthetic threshold"
    document["entries"][0]["locator"]["channel"] = channel
    path = tmp_path / "decimal-comma.csv"
    path.write_text(delimiter.join(("Channel", "Value", "Unit")) + "\n"
                    + delimiter.join((channel, "1,5", "V")) + "\n")
    report = CodingLibrary(document).assess(adaptation_map=read_adaptation_map(path))
    named = report["items"][0]["adaptation_observation"]
    assert named["state"] == "label_observed"
    assert named["matches"] == [{"channel": channel, "value": "1,5", "unit": "V",
                                 "extra_fields": ["V"], "row": 2}]


@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_ambiguous_headerless_csv_needs_explicit_header(tmp_path, delimiter):
    path = tmp_path / "ambiguous.csv"
    path.write_text(delimiter.join(("IDE00001-ENG00002-Synthetic threshold", "1,5", "V")) + "\n")
    with pytest.raises(WorkbenchError, match="Ambiguous CSV delimiter"):
        read_adaptation_map(path)


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_csv_exact_fields_multiline_values_and_physical_rows(document, tmp_path, delimiter):
    path = tmp_path / "literal.csv"
    raw = (';Synthetic private header\r\n\r\n'
           + delimiter.join(("Channel", "Value", "Unit")) + '\r\n'
           + delimiter.join(('" Apple_DIO "', '"  line1\r\n;not a comment\r\nline3  "', '" V "'))
           + '\r\n' + delimiter.join(("Other_feature", "OFF", "")) + '\r\n').encode()
    path.write_bytes(raw)
    adaptation_map = read_adaptation_map(path)
    assert adaptation_map["sha256"] == hashlib.sha256(raw).hexdigest()
    assert adaptation_map["records"] == [
        {"channel": " Apple_DIO ", "value": "  line1\r\n;not a comment\r\nline3  ", "unit": " V ",
         "extra_fields": [" V "], "row": 4},
        {"channel": "Other_feature", "value": "OFF", "unit": "", "extra_fields": [""], "row": 7},
    ]
    report = CodingLibrary(document).assess(adaptation_map=adaptation_map)
    assert report["items"][0]["adaptation_observation"]["matches"] == [adaptation_map["records"][0]]
    assert "Synthetic private header" not in str(adaptation_map)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_malformed_header_csv_cannot_fall_back_to_another_delimiter(tmp_path, delimiter):
    path = tmp_path / "malformed.csv"
    path.write_text(delimiter.join(("Channel", "Value", "Unit")) + "\n"
                    + delimiter.join(("IDE00001-ENG00002-Synthetic threshold", '"1,5', "V")) + "\n")
    with pytest.raises(WorkbenchError, match="Malformed CSV"):
        read_adaptation_map(path)


def test_conflicting_identity_signals_never_verify_a_family(document):
    doc = snapshot()
    doc["identity"]["sw_version"] = b"MHI2_SYNTHETIC".hex()
    report = CodingLibrary(document).assess(snapshot=doc)
    assert report["detected_family"] == "conflicting"
    assert all(item["compatibility"] == "family_unknown" for item in report["items"])
    assert all(item["feature_state"] == "unverified" and not item["writable"] for item in report["items"])
    assert report["coverage"]["vehicle_verified"] == report["coverage"]["writable"] == 0
