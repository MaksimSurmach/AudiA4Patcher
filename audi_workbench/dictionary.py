"""Evidence-backed labels and exact-match write definitions.

This module deliberately cannot infer a feature name from changing raw bytes.
An editable JSON file is a local review input, not a trusted authorization token.
The write path validates the complete entry again immediately before use.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

from .model import ECU, REQUIRED_IDENTITY, SafetyError, WorkbenchError

SCHEMA = "audi-workbench.dictionary/v1"
CONFIDENCE = {"standard", "source-confirmed", "vehicle-observed", "experimental", "unknown", "demo"}


def _hex(value: Any, label: str, *, size: int | None = None) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9A-Fa-f]{2})+", value):
        raise ValueError(f"{label} must be a nonempty complete raw hex value")
    raw = bytes.fromhex(value)
    if size is not None and len(raw) != size:
        raise ValueError(f"{label} must contain exactly {size} bytes")
    return raw


def _shift(mask: int) -> int:
    return (mask & -mask).bit_length() - 1


def _validate_entry(entry: dict[str, Any]) -> None:
    if not isinstance(entry, dict):
        raise TypeError("each dictionary entry must be an object")
    for key in ("id", "name", "ecu", "did", "confidence"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise ValueError(f"entry needs a nonempty {key}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", entry["id"]):
        raise ValueError("entry id may contain letters, numbers, dot, underscore and dash")
    if not re.fullmatch(r"[0-9A-F]{4}", entry["did"]):
        raise ValueError("DID must be four uppercase hex digits")
    if not re.fullmatch(r"(?:[0-9A-F]{2}|\*)", entry["ecu"]):
        raise ValueError("ECU must be two uppercase hex digits or * for read-only labels")
    if entry["confidence"] not in CONFIDENCE:
        raise ValueError("invalid confidence classification")
    source = entry.get("source")
    if not isinstance(source, list) or not source or any(not isinstance(s, str) or not s.strip() for s in source):
        raise ValueError("each entry needs at least one explicit source")
    if "writable" in entry and type(entry["writable"]) is not bool:
        raise ValueError("writable must be a boolean")
    if "read_by_default" in entry and type(entry["read_by_default"]) is not bool:
        raise ValueError("read_by_default must be a boolean")
    for key in ("category", "description"):
        if key in entry and (not isinstance(entry[key], str) or not entry[key].strip()):
            raise ValueError(f"{key} must be a nonempty string")
    if entry.get("origin") not in (None, "ble", "demo"):
        raise ValueError("origin must be ble or demo")
    length = entry.get("length")
    if length is not None and (type(length) is not int or not 1 <= length <= 4092):
        raise ValueError("length must be a positive number of data bytes up to 4092")
    if entry.get("encoding") not in (None, "ascii", "hex"):
        raise ValueError("only ascii and hex encoding are supported")
    fields = entry.get("fields", [])
    if not isinstance(fields, list):
        raise TypeError("fields must be a list")
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str) or not field["name"].strip():
            raise ValueError("each field needs a name")
        offset, mask = field.get("offset"), field.get("mask")
        if type(offset) is not int or offset < 0 or (length is not None and offset >= length):
            raise ValueError("field offset is outside its declared payload")
        if type(mask) is not int or not 1 <= mask <= 255:
            raise ValueError("field mask must be 1..255")
        values = field.get("values", {})
        if not isinstance(values, dict):
            raise TypeError("field values must map decimal numbers to labels")
        for key, value in values.items():
            if not isinstance(key, str) or not key.isdecimal() or str(int(key)) != key:
                raise ValueError("enum keys must be canonical decimal strings")
            if int(key) > (mask >> _shift(mask)) or not isinstance(value, str) or not value.strip():
                raise ValueError("enum value is outside the bit mask or has no label")
    applies = entry.get("applies_to")
    if applies is not None:
        if not isinstance(applies, dict) or not applies:
            raise ValueError("applies_to must be an exact nonempty raw identity mapping")
        for key, value in applies.items():
            if not isinstance(key, str):
                raise TypeError("identity keys must be strings")
            _hex(value, f"applies_to.{key}")
    # Feature maps, including read-only ones, must carry an exact version match.
    # General VAG identity labels remain useful without per-vehicle fingerprints.
    if fields and (not applies or any(not applies.get(k) for k in REQUIRED_IDENTITY)):
        raise ValueError("feature fields require the complete exact ECU identity")
    if entry.get("writable", False):
        if entry["ecu"] == "*":
            raise ValueError("writable entries cannot target all ECUs")
        if entry.get("origin") not in ("ble", "demo"):
            raise ValueError("writable entry must have explicit origin")
        if entry.get("verification") != "locally-verified":
            raise ValueError("writable entry needs locally-verified status")
        if entry["confidence"] in ("unknown", "experimental"):
            raise ValueError("unknown or experimental meanings cannot authorize writes")
        if entry["confidence"] == "demo" and entry["origin"] != "demo":
            raise ValueError("demo definitions cannot authorize physical writes")
        if not applies or any(not applies.get(k) for k in REQUIRED_IDENTITY):
            raise ValueError("writable entry needs complete exact identity")
        if length is None:
            raise ValueError("writable entry needs exact length")
        mask = _hex(entry.get("allowed_mask"), "allowed_mask", size=length)
        if not any(mask):
            raise ValueError("allowed_mask does not permit any change")
        for key in ("tx", "rx"):
            if type(entry.get(key)) is not int or not 0 <= entry[key] <= 0x7FF:
                raise ValueError("writable entry needs exact 11-bit tx/rx addresses")
        if entry["tx"] == entry["rx"]:
            raise ValueError("tx and rx must differ")
        if "session" not in entry or not (entry["session"] is None or
                                          (type(entry["session"]) is int and entry["session"] == 3)):
            raise ValueError("session must explicitly be null or 3")
        if entry.get("security") != "none":
            raise ValueError("only explicitly unprotected writes are implemented")
        evidence = entry.get("evidence")
        if not isinstance(evidence, str) or len(evidence.strip()) < 16:
            raise ValueError("writable entry needs a substantive evidence description")


class Dictionary:
    def __init__(self, entries: list[dict[str, Any]]):
        self.entries = deepcopy(entries)
        seen = set()
        try:
            for entry in self.entries:
                _validate_entry(entry)
                if entry["id"] in seen:
                    raise ValueError(f"duplicate entry id: {entry['id']}")
                seen.add(entry["id"])
        except (ValueError, TypeError) as exc:
            raise WorkbenchError(f"Invalid dictionary: {exc}") from exc

    @classmethod
    def load(cls, path: Path | None = None) -> Dictionary:
        try:
            if path is None:
                raw = files("audi_workbench").joinpath("data/default.json").read_text(encoding="utf-8")
            else:
                raw = Path(path).read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("entries"), list):
                raise ValueError(f"expected schema {SCHEMA} and an entries array")
            return cls(data["entries"])
        except (OSError, ValueError, TypeError) as exc:
            raise WorkbenchError(f"Cannot load dictionary: {exc}") from exc

    def entry(self, entry_id: str) -> dict[str, Any]:
        for entry in self.entries:
            if entry["id"] == entry_id:
                return deepcopy(entry)
        raise WorkbenchError(f"Unknown dictionary entry: {entry_id}")

    def read_dids(self, ecu_key: str, kind: str | None = None) -> list[int]:
        """Return default reads; optional catalog and synthetic records stay opt-in or isolated."""
        return sorted({
            int(entry["did"], 16) for entry in self.entries
            if entry["ecu"] in ("*", ecu_key)
            and entry.get("read_by_default", True)
            and (entry.get("origin") is None or entry.get("origin") == (kind or "ble"))
        })

    @staticmethod
    def _matches(entry: dict, identity: dict, ecu: dict, kind: str) -> bool:
        if entry["ecu"] not in ("*", ecu.get("key")):
            return False
        if entry.get("origin") is not None and entry["origin"] != kind:
            return False
        if any(entry.get(key) is not None and entry[key] != ecu.get(key) for key in ("tx", "rx")):
            return False
        try:
            return all(_hex(identity.get(key), f"identity.{key}") == _hex(expected, key)
                       for key, expected in entry.get("applies_to", {}).items())
        except (ValueError, TypeError):
            return False

    def decode(self, snapshot: dict) -> list[dict[str, Any]]:
        """Preserve unknowns, identity mismatches and unavailable values explicitly."""
        rows = []
        kind = snapshot.get("origin", {}).get("kind", "unknown")
        ecu, identity = snapshot.get("ecu", {}), snapshot.get("identity", {})
        for did, record in snapshot.get("records", {}).items():
            relevant = [entry for entry in self.entries
                        if entry["did"] == did and entry["ecu"] in ("*", ecu.get("key"))
                        and (entry.get("origin") is None or entry["origin"] == kind)]
            if not relevant:
                rows.append({"entry_id": None, "name": "Unknown DID", "did": did,
                             "status": "unknown" if record.get("ok") else "unavailable",
                             "value": "Unknown meaning" if record.get("ok") else record.get("error", "Read failed"),
                             "raw": record.get("hex", ""), "confidence": "unknown", "source": [], "writable": False})
                continue
            for entry in relevant:
                base = {"entry_id": entry["id"], "name": entry["name"], "did": did,
                        "raw": record.get("hex", ""), "confidence": entry["confidence"],
                        "source": list(entry["source"]), "writable": False}
                if not record.get("ok"):
                    rows.append({**base, "status": "unavailable", "value": record.get("error", "Read failed")})
                    continue
                if not self._matches(entry, identity, ecu, kind):
                    rows.append({**base, "status": "unavailable", "value": "Definition does not match ECU identity"})
                    continue
                try:
                    value = _hex(record.get("hex"), "record value", size=entry.get("length"))
                except (ValueError, TypeError) as exc:
                    rows.append({**base, "status": "unavailable", "value": str(exc)})
                    continue
                base["writable"] = bool(entry.get("writable", False))
                fields = entry.get("fields", [])
                if fields:
                    for field in fields:
                        if field["offset"] >= len(value):
                            rows.append({**base, "field": field["name"], "status": "unavailable",
                                         "value": "Payload too short for field", "writable": False})
                            continue
                        number = (value[field["offset"]] & field["mask"]) >> _shift(field["mask"])
                        label = field.get("values", {}).get(str(number))
                        if field.get("values") and label is None:
                            rows.append({**base, "field": field["name"], "status": "unknown",
                                         "value": f"Unknown enum value {number}", "writable": False})
                        else:
                            rows.append({**base, "field": field["name"], "status": "decoded",
                                         "value": label if label is not None else number,
                                         "offset": field["offset"], "mask": field["mask"]})
                elif entry.get("encoding") == "ascii":
                    # The raw hex always remains unmodified in the snapshot and row.
                    rows.append({**base, "status": "decoded", "value": value.decode("ascii", errors="replace").rstrip("\x00 ")})
                else:
                    rows.append({**base, "status": "unknown" if entry["confidence"] == "unknown" else "raw",
                                 "value": "Raw bytes; no verified feature map"})
        return rows

    def check_write(self, entry: dict, identity: dict[str, str], ecu: ECU, kind: str,
                    before: bytes, after: bytes) -> None:
        if not entry.get("writable", False):
            raise SafetyError("This dictionary entry is read-only")
        try:
            _validate_entry(entry)
        except (ValueError, TypeError) as exc:
            raise SafetyError(f"Invalid write definition: {exc}") from exc
        if entry.get("origin") != kind:
            raise SafetyError("Definition origin does not match the connected link")
        if not self._matches(entry, identity, ecu.as_dict(), kind):
            raise SafetyError("Write definition does not match exact ECU identity or CAN addresses")
        if len(before) != entry["length"] or len(after) != entry["length"]:
            raise SafetyError("Write must preserve the exact declared payload length")
        mask = bytes.fromhex(entry["allowed_mask"])
        if any((old ^ new) & (~allowed & 0xFF) for old, new, allowed in zip(before, after, mask)):
            raise SafetyError("Change modifies bits outside the approved allowed_mask")
        for field in entry.get("fields", []):
            current = (before[field["offset"]] & field["mask"]) >> _shift(field["mask"])
            if field.get("values") and str(current) not in field["values"]:
                raise SafetyError(
                    f"Current value for {field['name']} cannot be restored by this definition: {current}"
                )
            number = (after[field["offset"]] & field["mask"]) >> _shift(field["mask"])
            if field.get("values") and str(number) not in field["values"]:
                raise SafetyError(f"Unsupported value for {field['name']}: {number}")
