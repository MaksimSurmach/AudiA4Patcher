"""Turn observed records into a reviewable, read-only dictionary draft."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .dictionary import SCHEMA, Dictionary
from .model import IDENTITY_DIDS, SafetyError
from .store import Store, now


def draft_dictionary(snapshot_path: Path | str, output: Path | str, dictionary: Dictionary) -> Path:
    snapshot = Store.read(snapshot_path)
    if snapshot.get("schema") != "audi-workbench.snapshot/v1":
        raise SafetyError("Dictionary drafting requires a snapshot")
    kind = snapshot["origin"]["kind"]
    if kind not in ("demo", "ble"):
        raise SafetyError("Unknown snapshot origin")
    entries = copy.deepcopy(dictionary.entries)
    for did, record in snapshot["records"].items():
        if record.get("ok") is not True or int(did, 16) in IDENTITY_DIDS.values():
            continue
        raw = bytes.fromhex(record["hex"])
        entry_id = f"candidate.{snapshot['ecu']['key']}.{did}"
        if any(entry["id"] == entry_id for entry in entries):
            raise SafetyError("Candidate already exists in this dictionary; review its current definition")
        entries.append({
            "id": entry_id, "name": f"Observed {snapshot['ecu']['key']}/{did}; meaning unverified",
            "ecu": snapshot["ecu"]["key"], "tx": snapshot["ecu"]["tx"], "rx": snapshot["ecu"]["rx"],
            "did": did, "origin": kind, "length": len(raw), "encoding": "hex", "writable": False,
            "confidence": "unknown", "source": [f"Local snapshot SHA256 {snapshot['sha256']}"],
            "applies_to": copy.deepcopy(snapshot["identity"]), "observed_hex": record["hex"],
            "notes": "Read evidence only. Obtain an exact definition before adding names, masks or writable status.",
        })
    # No generated candidate gets a writable mask or reviewed status.
    Dictionary(entries)
    document = {"schema": SCHEMA, "created_at": now(), "source_snapshot_sha256": snapshot["sha256"],
                "entries": entries}
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
