"""Scriptable CLI. JSON results go to stdout; errors go to stderr."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

from . import __version__
from .dictionary import SCHEMA, Dictionary
from .model import ECUS, IDENTITY_DIDS, SafetyError, WorkbenchError
from .store import Store, now


def hex_did(value: str) -> int:
    try:
        number = int(value, 16)
        if not 0 <= number <= 0xFFFF:
            raise ValueError()
        return number
    except ValueError as exc:
        raise argparse.ArgumentTypeError("DID must be hexadecimal, e.g. 0600 or F190") from exc


def number(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use decimal or 0x-prefixed hexadecimal") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="audi-a4-patcher",
        description="Audi A4 Patcher for B9 / MHS2; not hardware-tested.",
        epilog=("Default real dictionary entries are read-only. ELM CAF1 requests are limited to 7 UDS bytes "
                "(4 bytes of a writable DID value). Long coding is read/preview only on BLE. "
                "Snapshots are partial configuration backups, not flash images. "
                "Without a command, the terminal interface opens. Global options precede the command. "
                "Byte/bit indexes start at zero; bit 0 is LSB."),
    )
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--data-dir", type=Path, default=Path.home() / ".local/share/audi-workbench")
    result.add_argument("--dictionary", type=Path, help="Reviewed local JSON dictionary; replaces defaults")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="Persistent simulator; never connects to a car")
    mode.add_argument("--device", help="Explicit BLE address/UUID or unique adapter name")
    commands = result.add_subparsers(dest="command")
    scan = commands.add_parser("scan", help="Discover BLE devices without connecting")
    scan.add_argument("--timeout", type=float, default=8.0)
    capture = commands.add_parser("capture", help="Read identification and selected DIDs; save raw evidence")
    capture.add_argument("--ecu", choices=list(ECUS), default="5F")
    capture.add_argument("--did", type=hex_did, action="append", help="Additional known DID; repeat as needed")
    capture.add_argument("--dids-file", type=Path, help="Explicit DIDs as a JSON list or one hex DID per line; max 256")
    capture.add_argument("--only", action="store_true", help="Read identity + explicit DIDs; omit dictionary defaults")
    capture.add_argument("--dtc", action="store_true", help="Also read diagnostic trouble-code status; never clear it")
    commands.add_parser("collect", help="Read 5F, 19 and 01 in independent sessions, then save a support ZIP")
    inspect = commands.add_parser("inspect", help="Inspect a saved snapshot and decode known fields offline")
    inspect.add_argument("snapshot", type=Path)
    diff = commands.add_parser("diff", help="Compare two captured snapshots offline")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    plan = commands.add_parser("plan", help="Preview a proposed bit/byte/value change; does not connect or write")
    plan.add_argument("snapshot", type=Path)
    target = plan.add_mutually_exclusive_group(required=True)
    target.add_argument("--entry", help="Dictionary entry ID, e.g. demo.display")
    target.add_argument("--did", type=hex_did, help="Raw DID preview; does not grant write permission")
    edit = plan.add_mutually_exclusive_group(required=True)
    edit.add_argument("--hex", dest="value_hex", help="Full replacement value, unchanged length")
    edit.add_argument("--byte", type=number, help="Zero-based byte offset")
    plan.add_argument("--bit", type=int, choices=range(8), help="Zero-based bit within that byte (0 = LSB)")
    plan.add_argument("--value", type=number, help="Byte value 0..255 or bit value 0/1")
    apply = commands.add_parser("apply", help="Apply one reviewed plan after backup and fresh preflight")
    apply.add_argument("plan", type=Path)
    apply.add_argument("--enable-writes", action="store_true", help="Explicitly permit this one write operation")
    apply.add_argument("--confirm", required=True, help="Exact confirmation phrase printed in the plan")
    restore = commands.add_parser("restore", help="Build a restore plan from a verified transaction; no write")
    restore.add_argument("transaction", type=Path)
    reconcile = commands.add_parser("reconcile", help="Read current state after an uncertain transaction")
    reconcile.add_argument("transaction", type=Path)
    commands.add_parser("list", help="List saved snapshots, plans, transactions and exports in the data directory")
    commands.add_parser("export", help="Create a local support ZIP with raw logs and snapshots (includes VIN)")
    commands.add_parser("dictionary", help="Show dictionary entries and their evidence/permissions")
    template = commands.add_parser("dictionary-template", help="Draft read-only unknown entries from a snapshot")
    template.add_argument("snapshot", type=Path)
    template.add_argument("--output", type=Path, required=True, help="New JSON file; existing files are never overwritten")
    commands.add_parser("tui", help="Open the terminal interface; supports offline inspection too")
    return result


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False))


def _artifact(store: Store, path: Path) -> dict:
    return {"artifact": str(path), "document": store.read(path)}


def _link(args, store: Store):
    if args.demo:
        from .simulator import DemoLink
        return DemoLink(store.root / "demo-state.json", trace=store.trace)
    if args.device:
        from .transport import BLELink
        return BLELink(args.device, trace=store.trace)
    return None


async def _online(workbench, args):
    if workbench.link is None:
        raise WorkbenchError("Choose --demo or an explicit --device before connecting")
    async with workbench:
        if args.command == "capture":
            return await workbench.capture(args.ecu, dids=args.capture_dids, include_defaults=not args.only,
                                           include_dtc=args.dtc)
        if args.command == "apply":
            return await workbench.apply(args.plan, confirmation=args.confirm, enable_writes=args.enable_writes)
        if args.command == "reconcile":
            return await workbench.reconcile(args.transaction)
    raise WorkbenchError("Unknown online operation")


def _capture_dids(args) -> list[int]:
    values = list(args.did or [])
    if args.dids_file is not None:
        if args.dids_file.stat().st_size > 1024 * 1024:
            raise WorkbenchError("DID list is too large")
        text = args.dids_file.read_text(encoding="utf-8")
        if text.lstrip().startswith("["):
            items = json.loads(text)
            if not isinstance(items, list):
                raise WorkbenchError("Expected a JSON list of hexadecimal DID strings or integers")
        else:
            items = [line.split("#", 1)[0].strip() for line in text.splitlines()]
            items = [item for item in items if item]
        for item in items:
            if isinstance(item, str):
                values.append(hex_did(item))
            elif type(item) is int and 0 <= item <= 0xFFFF:
                values.append(item)
            else:
                raise WorkbenchError("DID list must contain hexadecimal strings or 16-bit integers")
    if len(set(values)) > 256:
        raise WorkbenchError("Capture is limited to 256 explicit DIDs")
    return sorted(set(values))


async def _collect(args, store, dictionary):
    from .service import Workbench
    if not (args.demo or args.device):
        raise WorkbenchError("Choose --demo or an explicit --device before connecting")
    captures = []
    for ecu in ("5F", "19", "01"):
        try:
            async with Workbench(store, dictionary, _link(args, store)) as wb:
                # The first visit to the car is scarce: also read the opt-in identification catalog.
                path = await wb.capture(ecu, dids=[int(e["did"], 16) for e in dictionary.entries
                                                   if e["ecu"] in ("*", ecu) and e.get("origin") is None])
            snapshot = store.read(path)
            captures.append({"ecu": ecu, "artifact": str(path), "complete": snapshot.get("complete", False),
                             "stopped_reason": snapshot.get("stopped_reason")})
        except (WorkbenchError, OSError, ValueError) as exc:
            captures.append({"ecu": ecu, "complete": False, "error": str(exc)})
    exported = store.export(dictionary.document())
    return {"captures": captures, "artifact": str(exported), "uploaded": False, "redacted": False,
            "complete": all(item["complete"] for item in captures)}


def list_artifacts(store: Store) -> dict:
    """Newest first, so an agent can pick the latest artifact without parsing filenames."""
    def summary(kind: str, path: Path) -> dict:
        try:
            doc = store.read(path)
        except (OSError, WorkbenchError) as exc:
            return {"artifact": str(path), "error": str(exc)}
        item = {"artifact": str(path), "origin": doc.get("origin", {}).get("kind"),
                "ecu": doc.get("ecu", {}).get("key")}
        if kind == "snapshots":
            records = doc.get("records", {}).values()
            item.update(captured_at=doc.get("captured_at"), complete=doc.get("complete"),
                        successful=sum(bool(r.get("ok")) for r in records), attempted=len(records),
                        stopped_reason=doc.get("stopped_reason"), purpose=doc.get("purpose"))
        elif kind == "plans":
            item.update(created_at=doc.get("created_at"), action=doc.get("action"), entry_id=doc.get("entry_id"),
                        did=doc.get("did"), eligible=doc.get("eligible"), blocked_reasons=doc.get("blocked_reasons"))
        else:
            item.update(created_at=doc.get("created_at"), status=doc.get("status"), did=doc.get("did"),
                        entry_id=doc.get("entry_id"), error=doc.get("error"))
        return item
    result = {"data_dir": str(store.root)}
    for kind in ("snapshots", "plans", "transactions"):
        result[kind] = [summary(kind, path) for path in reversed(store.files(kind))]
    result["exports"] = [str(path) for path in sorted((store.root / "exports").glob("*.zip"), reverse=True)]
    return result


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


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    args.command = args.command or "tui"
    if args.command == "plan":
        if args.byte is not None and args.value is None:
            cli.error("--byte requires --value")
        if args.value_hex is not None and (args.bit is not None or args.value is not None):
            cli.error("--hex cannot be combined with --bit or --value")
    try:
        if args.command == "apply" and args.device and not sys.stdin.isatty():
            # Agents and scripts may capture, inspect and plan; a person applies to the real car.
            raise SafetyError("Real-vehicle apply needs an interactive terminal; run it yourself or use the TUI")
        if args.command == "capture":
            args.capture_dids = _capture_dids(args)
        if args.command == "scan":
            from .transport import scan_devices
            if not 1 <= args.timeout <= 60:
                raise WorkbenchError("Scan timeout must be between 1 and 60 seconds")
            emit({"devices": asyncio.run(scan_devices(args.timeout))})
            return 0
        dictionary = Dictionary.load(args.dictionary)
        store = Store(args.data_dir)
        if args.command == "dictionary":
            emit(dictionary.document())
            return 0
        if args.command == "inspect":
            snapshot = store.read(args.snapshot)
            if snapshot.get("schema") != "audi-workbench.snapshot/v1":
                raise WorkbenchError("Expected a snapshot file")
            emit({"snapshot": snapshot, "decoded": dictionary.decode(snapshot)})
            return 0
        if args.command == "dictionary-template":
            path = draft_dictionary(args.snapshot, args.output, dictionary)
            emit({"artifact": str(path), "candidate_permissions": "read-only", "semantics": "unknown"})
            return 0
        if args.command == "list":
            emit(list_artifacts(store))
            return 0
        if args.command == "export":
            path = store.export(dictionary.document())
            emit({"artifact": str(path), "redacted": False, "uploaded": False})
            return 0
        if args.command == "collect":
            result = asyncio.run(_collect(args, store, dictionary))
            emit(result)
            return 0 if result["complete"] else 4
        from .service import Workbench
        wb = Workbench(store, dictionary, _link(args, store))
        if args.command == "diff":
            emit(Workbench.diff(args.before, args.after))
        elif args.command == "plan":
            path = wb.make_plan(args.snapshot, entry_id=args.entry, did=args.did, value_hex=args.value_hex,
                                byte=args.byte, bit=args.bit, value=args.value)
            emit(_artifact(store, path))
        elif args.command == "restore":
            emit(_artifact(store, wb.make_restore(args.transaction)))
        elif args.command == "tui":
            try:
                from .tui import run_tui
            except ImportError as exc:
                raise WorkbenchError("TUI dependencies are missing. Run: uv sync --locked") from exc
            run_tui(wb)
        else:
            path = asyncio.run(_online(wb, args))
            result = _artifact(store, path)
            if args.command == "capture":
                records = result["document"].get("records", {})
                result["coverage"] = {"successful": sum(bool(item.get("ok")) for item in records.values()),
                                      "attempted": len(records), "complete": result["document"].get("complete", False)}
            emit(result)
            if args.command == "apply" and result["document"].get("status") != "verified":
                return 3
            if args.command == "capture" and not result["document"].get("complete"):
                return 4
        return 0
    except KeyboardInterrupt:
        print("Interrupted. Inspect transaction evidence before attempting another write.", file=sys.stderr)
        return 130
    except (WorkbenchError, OSError, ValueError, KeyError, argparse.ArgumentTypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
