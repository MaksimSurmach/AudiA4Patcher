"""Offline research library and evidence comparison; never a write definition.

Forum labels, dataset addresses, and IDE/ENG identifiers are not UDS addresses.
Observed bytes and named map values are reported separately from claimed meaning.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from importlib.resources import files
from io import StringIO
from itertools import chain
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .model import WorkbenchError

SCHEMA = "audi-workbench.library/v1"
REPORT_SCHEMA = "audi-workbench.library-report/v1"
STATUSES = ("documented", "reported", "conflicting", "out_of_scope")
_IDENTIFIER = re.compile(r"\b(?:IDE|ENG|MAS)\d+\b", re.IGNORECASE)
_ID = re.compile(r"[a-zA-Z0-9_.-]+")


def _text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")


def _texts(value: Any, label: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{label} must be {'a nonempty' if nonempty else 'a'} list")
    for item in value:
        _text(item, label)


def _validate(document: dict) -> None:
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError(f"expected schema {SCHEMA}")
    _text(document.get("version"), "version")
    _text(document.get("scope"), "scope")
    if document.get("index_base") != 0 or type(document.get("index_base")) is not int:
        raise ValueError("index_base must be zero")
    sources, entries = document.get("sources"), document.get("entries")
    if not isinstance(sources, list) or not isinstance(entries, list):
        raise TypeError("sources and entries must be lists")
    source_ids = set()
    for source in sources:
        if not isinstance(source, dict):
            raise TypeError("each source must be an object")
        for key in ("id", "title", "url", "kind", "access"):
            _text(source.get(key), f"source.{key}")
        if not _ID.fullmatch(source["id"]) or source["id"] in source_ids:
            raise ValueError("source IDs must be unique identifiers")
        url = urlsplit(source["url"])
        if url.scheme not in ("https", "http") or not url.netloc or url.username or url.password:
            raise ValueError("source URL must be a public HTTP(S) reference")
        if source["access"] not in ("full", "excerpt", "blocked"):
            raise ValueError("source access must be full, excerpt, or blocked")
        source_ids.add(source["id"])
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("each library entry must be an object")
        for key in ("id", "name", "category", "kind", "ecu", "status", "summary"):
            _text(entry.get(key), key)
        if not _ID.fullmatch(entry["id"]) or entry["id"] in seen:
            raise ValueError("entry IDs must be unique identifiers")
        seen.add(entry["id"])
        if not re.fullmatch(r"[0-9A-F]{2}", entry["ecu"]):
            raise ValueError("ECU must be two uppercase hex digits")
        if entry["kind"] not in ("coding", "adaptation", "procedure"):
            raise ValueError("invalid library entry kind")
        if entry["status"] not in STATUSES or entry.get("writable") is not False:
            raise ValueError("research entries must have a research status and writable=false")
        if entry.get("verified_on_target", False) is not False:
            raise ValueError("research entries cannot assert target verification")
        for key in ("families", "platforms", "limitations", "verification_questions"):
            _texts(entry.get(key), key, nonempty=True)
        for key in ("reported_values", "prerequisites"):
            _texts(entry.get(key), key)
        locator = entry.get("locator")
        if not isinstance(locator, dict):
            raise TypeError("entry needs a locator object")
        for key in ("channel", "did", "byte", "bit", "mask"):
            if key not in locator:
                raise ValueError(f"locator.{key} must be explicit, using null when unknown")
        if locator["channel"] is not None:
            _text(locator["channel"], "channel")
        if locator["did"] is not None and (
            not isinstance(locator["did"], str) or not re.fullmatch(r"[0-9A-F]{4}", locator["did"])
        ):
            raise ValueError("locator DID must be four uppercase hex digits or null")
        for key, low, high in (("byte", 0, 4091), ("bit", 0, 7), ("mask", 1, 255)):
            value = locator[key]
            if value is not None and (type(value) is not int or not low <= value <= high):
                raise ValueError(f"invalid locator {key}")
        if (locator["bit"] is not None or locator["mask"] is not None) and locator["byte"] is None:
            raise ValueError("a bit or mask needs a byte offset")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("each entry needs claim-level evidence")
        for item in evidence:
            if not isinstance(item, dict) or item.get("source_id") not in source_ids:
                raise ValueError("evidence must reference an existing source")
            _text(item.get("claim"), "evidence.claim")
            _text(item.get("locator_hint"), "evidence.locator_hint")


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _search_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_search_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_search_text(item) for item in value)
    return str(value).casefold() if value is not None else ""


def _csv_rows(text: str, delimiter: str):
    lines = enumerate(StringIO(text, newline=""), 1)
    for line_number, first_line in lines:
        if not first_line.strip() or first_line.lstrip().startswith(";"):
            continue
        # The reader consumes a whole record, so comments inside quoted values
        # stay data and the next iteration starts at a physical record boundary.
        record_lines = chain((first_line,), (line for _, line in lines))
        yield line_number, next(csv.reader(record_lines, delimiter=delimiter, strict=True))


def read_adaptation_map(path: Path | str) -> dict:
    """Read a VCDS CSV or a Channel,Value,Unit CSV without inventing addresses.

    Header comments may contain a VIN. They are deliberately not copied into the
    report. The input checksum retains provenance; the original stays local.
    """
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise WorkbenchError("Adaptation map exceeds the 10 MiB input limit")
    try:
        text = raw.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
        encoding = "cp1252"
    candidates = []
    for delimiter in (",", ";", "\t"):
        records = []
        header = False
        has_unit_column = False
        try:
            for line_number, row in _csv_rows(text, delimiter):
                if len(row) < 2:
                    continue
                if _normal(row[0]) in ("channel", "name", "adaptation") and _normal(row[1]) in (
                    "value", "storedvalue", "currentvalue"
                ):
                    header = True
                    has_unit_column = len(row) > 2 and _normal(row[2]) in ("unit", "units")
                    continue
                channel = row[0]
                if not channel.strip() or (not header and not _IDENTIFIER.match(channel.strip())):
                    continue
                records.append({"channel": channel, "value": row[1],
                                "unit": row[2] if has_unit_column and len(row) > 2 else "",
                                "extra_fields": row[2:], "row": line_number})
        except csv.Error as exc:
            if header:
                raise WorkbenchError(f"Malformed CSV after a recognized header: {exc}") from exc
            continue
        candidates.append((header, records))
    preferred = ([records for header, records in candidates if header]
                 or [records for _, records in candidates if records])
    if len(preferred) > 1:
        raise WorkbenchError("Ambiguous CSV delimiter; add a Channel,Value,Unit header using the correct "
                             "delimiter to a working copy of the map")
    records = preferred[0] if preferred else []
    if not records:
        raise WorkbenchError("No adaptation rows found; use VCDS CSV or a Channel,Value,Unit header")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "encoding": encoding,
            "records": records, "identity_verified": False}


def _map_matches(channel: str, adaptation_map: dict) -> tuple[list[dict], str]:
    rows = adaptation_map.get("records", [])
    identifiers = _IDENTIFIER.findall(channel.upper())
    if len(identifiers) >= 2:
        rows = [row for row in rows if len(_IDENTIFIER.findall(row["channel"])) < 2
                or _IDENTIFIER.findall(row["channel"].upper()) == identifiers]
    if identifiers:
        # IDE alone may name an entire group, so only use identifier equality
        # when both identifiers and the normalized label agree, or two IDs exist.
        matches = [row for row in rows if _IDENTIFIER.findall(row["channel"].upper()) == identifiers
                   and (len(identifiers) >= 2 or _normal(_IDENTIFIER.sub("", row["channel"]))
                        == _normal(_IDENTIFIER.sub("", channel)))]
        if matches:
            return matches, "identifier-label"
    label = _normal(_IDENTIFIER.sub("", channel))
    matches = [row for row in rows if _normal(_IDENTIFIER.sub("", row["channel"])) == label]
    if matches:
        return matches, "full-label"
    # Short labels from human guides can only produce tentative, unique matches.
    short = "-" not in _IDENTIFIER.sub("", channel).strip("- ")
    leaf = _normal(channel.split("-")[-1])
    matches = [row for row in rows if _normal(row["channel"].split("-")[-1]) == leaf
               and (short or "-" not in _IDENTIFIER.sub("", row["channel"]).strip("- "))]
    return matches, "leaf-label-only" if matches else "none"


def _identity_text(snapshot: dict) -> dict[str, str]:
    result = {}
    for key in ("part_number", "sw_version", "hw_part_number", "hw_version", "system_name",
                "asam_name", "asam_version"):
        raw = snapshot.get("identity", {}).get(key)
        if isinstance(raw, str):
            try:
                result[key] = bytes.fromhex(raw).decode("ascii").rstrip("\x00 ")
            except (ValueError, UnicodeDecodeError):
                pass
    return result


def _family(snapshot: dict) -> str | None:
    if snapshot.get("origin", {}).get("kind") == "demo":
        return "demo"
    identity = _identity_text(snapshot)
    signals = set()
    for value in identity.values():
        upper = value.upper()
        for family in ("MHS2", "MHI2", "MH2P", "MIB3"):
            if upper.startswith(family):
                signals.add(family)
        if upper.startswith(("MU-HS", "EV_MUHIGSCL6C3DELP")):
            signals.add("MHS2")
    return next(iter(signals)) if len(signals) == 1 else ("conflicting" if signals else None)


def _raw_observation(snapshot: dict | None, locator: dict, compatibility: str) -> dict:
    if not snapshot or not locator["did"] or compatibility in ("ecu_mismatch", "simulator"):
        return {"state": "not_observed"}
    record = snapshot.get("records", {}).get(locator["did"])
    if record is None:
        return {"state": "not_observed"}
    if record.get("ok") is not True:
        return {"state": "read_failed", "error": record.get("error", "Unknown read error")}
    raw = record.get("hex", "")
    try:
        value = bytes.fromhex(raw)
    except (ValueError, TypeError) as exc:
        raise WorkbenchError("Snapshot contains malformed raw hex") from exc
    observation = {"state": "raw_observed", "did": locator["did"], "raw_hex": raw}
    offset = locator["byte"]
    if offset is None:
        return observation
    if offset >= len(value):
        observation["state"] = "payload_too_short"
        return observation
    observation["byte_index"] = offset
    observation["byte_hex"] = f"{value[offset]:02X}"
    if locator["bit"] is not None:
        observation["bit_value"] = (value[offset] >> locator["bit"]) & 1
    if locator["mask"] is not None:
        observation["masked_hex"] = f"{value[offset] & locator['mask']:02X}"
    return observation


class CodingLibrary:
    def __init__(self, document: dict):
        try:
            _validate(document)
        except (ValueError, TypeError, KeyError) as exc:
            raise WorkbenchError(f"Invalid research library: {exc}") from exc
        self._document = deepcopy(document)
        self.entries = self._document["entries"]
        self.sources = {source["id"]: source for source in self._document["sources"]}

    @classmethod
    def load(cls, path: Path | str | None = None) -> CodingLibrary:
        try:
            raw = (files("audi_workbench").joinpath("data/mhs2_library.json").read_text(encoding="utf-8")
                   if path is None else Path(path).read_text(encoding="utf-8"))
            return cls(json.loads(raw))
        except (OSError, ValueError, TypeError) as exc:
            raise WorkbenchError(f"Cannot load research library: {exc}") from exc

    def document(self) -> dict:
        return deepcopy(self._document)

    def summary(self) -> dict:
        return {"entries": len(self.entries), "sources": len(self.sources),
                "by_status": dict(sorted(Counter(e["status"] for e in self.entries).items())),
                "by_category": dict(sorted(Counter(e["category"] for e in self.entries).items())),
                "writable": 0, "vehicle_verified": 0}

    def entry(self, entry_id: str) -> dict:
        for entry in self.entries:
            if entry["id"] == entry_id:
                return deepcopy(entry)
        raise WorkbenchError(f"Unknown research entry: {entry_id}")

    def detail(self, entry_id: str) -> dict:
        entry = self.entry(entry_id)
        source_ids = dict.fromkeys(item["source_id"] for item in entry["evidence"])
        return {"entry": entry, "sources": [deepcopy(self.sources[key]) for key in source_ids]}

    def search(self, query: str = "", *, category: str | None = None, status: str | None = None,
               family: str | None = None, ecu: str | None = None) -> list[dict]:
        result = []
        terms = query.casefold().split()
        for entry in self.entries:
            if any(value is not None and entry[key].casefold() != value.casefold()
                   for key, value in (("category", category), ("status", status), ("ecu", ecu))):
                continue
            if family is not None and family.casefold() not in [f.casefold() for f in entry["families"]]:
                continue
            haystack = _search_text(self.detail(entry["id"]))
            if all(term in haystack for term in terms):
                result.append(deepcopy(entry))
        return sorted(result, key=lambda e: (e["category"], e["name"], e["id"]))

    def assess(self, *, snapshot: dict | None = None, adaptation_map: dict | None = None,
               entries: list[dict] | None = None) -> dict:
        """Compare local observations, retaining unknown semantics and provenance.

        A family match is a research filter, never an exact ECU definition match.
        No boolean feature state or executable write mask is generated here.
        """
        if snapshot is not None:
            if not isinstance(snapshot, dict) or snapshot.get("schema") != "audi-workbench.snapshot/v1":
                raise WorkbenchError("Library comparison requires a Workbench snapshot")
            if (any(not isinstance(snapshot.get(key), dict) for key in ("origin", "ecu", "identity", "records"))
                    or any(not isinstance(record, dict) for record in snapshot["records"].values())):
                raise WorkbenchError("Snapshot contains malformed identity or record metadata")
        family = _family(snapshot) if snapshot else None
        results = []
        for entry in self.entries if entries is None else entries:
            compatibility = "not_checked"
            if snapshot:
                if snapshot.get("ecu", {}).get("key") != entry["ecu"]:
                    compatibility = "ecu_mismatch"
                elif family == "demo":
                    compatibility = "simulator"
                elif family in (None, "conflicting"):
                    compatibility = "family_unknown"
                elif family in entry["families"]:
                    compatibility = "family_candidate"
                else:
                    compatibility = "family_mismatch"
            locator = entry["locator"]
            observation = _raw_observation(snapshot, locator, compatibility)
            named = {"state": "not_provided"}
            if adaptation_map is not None and locator["channel"]:
                matches, method = _map_matches(locator["channel"], adaptation_map)
                named = {"state": "ambiguous" if len(matches) > 1 else ("label_observed" if matches else "not_found"),
                         "method": method, "matches": deepcopy(matches), "identity_verified": False}
            questions = list(entry["verification_questions"])
            if locator["did"] is None and entry["kind"] != "procedure":
                questions.append("Obtain an independently evidenced UDS DID and payload codec; IDE/ENG is not a DID.")
            results.append({"entry_id": entry["id"], "name": entry["name"], "status": entry["status"],
                            "locator": deepcopy(locator), "reported_values": list(entry["reported_values"]),
                            "evidence": deepcopy(entry["evidence"]), "limitations": list(entry["limitations"]),
                            "compatibility": compatibility, "raw_observation": observation,
                            "adaptation_observation": named, "feature_state": "unverified", "writable": False,
                            "next_checks": questions})
        return {"schema": REPORT_SCHEMA, "library_version": self._document["version"],
                "library_sha256": hashlib.sha256(json.dumps(self._document, sort_keys=True,
                                                             ensure_ascii=False).encode()).hexdigest(),
                "snapshot_sha256": snapshot.get("sha256") if snapshot else None,
                "snapshot_identity": _identity_text(snapshot) if snapshot else {}, "detected_family": family,
                "adaptation_map_sha256": adaptation_map.get("sha256") if adaptation_map else None,
                "map_identity_verified": False, "items": results,
                "sources": [deepcopy(self.sources[key]) for key in dict.fromkeys(
                    evidence["source_id"] for item in results for evidence in item["evidence"])],
                "coverage": {"entries": len(results),
                             "raw_observed": sum(r["raw_observation"]["state"] == "raw_observed" for r in results),
                             "named_observed": sum(r["adaptation_observation"]["state"] == "label_observed"
                                                   for r in results),
                             "vehicle_verified": 0, "writable": 0},
                "notice": "Research comparison only. Family/label matches do not verify meaning, exact applicability, "
                          "licensing, hardware support, or write safety. No diagnostic requests were sent."}
