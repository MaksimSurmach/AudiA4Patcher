"""Read-only collection and a single-record, journaled write transaction."""
from __future__ import annotations

import asyncio
import copy
import hmac
import math
import re
import uuid
from pathlib import Path

from .dictionary import Dictionary
from .model import (
    ECU,
    ECUS,
    IDENTITY_DIDS,
    REQUIRED_IDENTITY,
    DiagnosticLink,
    NegativeResponse,
    NoData,
    ProtocolError,
    SafetyError,
    WorkbenchError,
    did_hex,
    parse_hex,
    uds_data,
)
from .store import Store, digest, now


class Workbench:
    def __init__(self, store: Store, dictionary: Dictionary, link: DiagnosticLink | None = None):
        self.store, self.dictionary, self.link = store, dictionary, link
        self._process_lock = None
        self._operation_lock = asyncio.Lock()
        self._opened = False

    async def __aenter__(self):
        if self.link is None:
            raise SafetyError("Select --demo or an explicit BLE --device for online operations")
        if self._opened:
            raise SafetyError("Session is already open")
        self._process_lock = self.store.lock()
        self._process_lock.__enter__()
        try:
            self.store.start_trace()
            self.link.trace = self.store.trace
            await self.link.open()
            self.store.trace({"event": "connected", "origin": self.link.kind, "adapter": self.link.metadata})
            self._opened = True
            return self
        except BaseException as exc:
            self._opened = False
            try:
                await self.link.close()
            except BaseException as cleanup_error:  # noqa: BLE001 - Preserve the original entry failure.
                exc.add_note(f"Diagnostic link cleanup failed: {cleanup_error}")
            try:
                self._process_lock.__exit__(None, None, None)
            except BaseException as cleanup_error:  # noqa: BLE001 - Preserve the original entry failure.
                exc.add_note(f"Session lock cleanup failed: {cleanup_error}")
            finally:
                self._process_lock = None
            raise

    async def __aexit__(self, *exc):
        try:
            if self.link:
                await self.link.close()
        finally:
            self._opened = False
            if self._process_lock:
                self._process_lock.__exit__(*exc)
                self._process_lock = None

    def _online(self):
        if not self._opened or self.link is None:
            raise SafetyError("Operation needs an open diagnostic session")

    @staticmethod
    def _ecu(value: dict | str) -> ECU:
        key = value.upper() if isinstance(value, str) else value.get("key", "").upper()
        if key not in ECUS:
            raise SafetyError(f"ECU {key!r} is not in the current Audi profile")
        ecu = ECUS[key]
        if isinstance(value, dict) and (value.get("tx") != ecu.tx or value.get("rx") != ecu.rx):
            raise SafetyError("CAN address differs from the selected ECU profile")
        return ecu

    async def _read(self, ecu: ECU, did: int) -> bytes:
        did_hex(did)
        request = b"\x22" + did.to_bytes(2, "big")
        result = uds_data(await self.link.request(ecu, request), request)
        if not result:
            raise ProtocolError("ECU returned an empty data record")
        return result

    async def _identity(self, ecu: ECU) -> tuple[dict, dict]:
        identity, records = {}, {}
        for name, did in IDENTITY_DIDS.items():
            try:
                data = await self._read(ecu, did)
                identity[name] = data.hex().upper()
                records[did_hex(did)] = {"ok": True, "hex": data.hex().upper(), "name": name,
                                         "display": data.decode("ascii", errors="replace").rstrip("\x00 ")}
            except (NegativeResponse, NoData) as exc:
                if isinstance(exc, NegativeResponse) and exc.code == 0x78:
                    raise
                records[did_hex(did)] = {"ok": False, "error": str(exc), "name": name}
        return identity, records

    def _snapshot(self, ecu: ECU, identity: dict, records: dict) -> dict:
        return {"schema": "audi-workbench.snapshot/v1", "captured_at": now(), "snapshot_id": uuid.uuid4().hex,
                "origin": {"kind": self.link.kind, "adapter": copy.deepcopy(self.link.metadata)},
                "ecu": ecu.as_dict(), "identity": identity, "records": records,
                "scope": "Only the successful records listed here; not a firmware or EEPROM image",
                "trace_file": str(self.store.trace_path)}

    async def capture(self, ecu_key: str = "5F", dids: list[int] | None = None,
                      include_defaults: bool = True, include_dtc: bool = False) -> Path:
        """Bounded harvest of explicit DIDs; no enumeration of unknown addresses."""
        self._online()
        ecu = self._ecu(ecu_key)
        requested = set(dids or [])
        if include_defaults:
            requested.update(self.dictionary.read_dids(ecu.key, self.link.kind))
        for did in requested:
            did_hex(did)
        requested.update(IDENTITY_DIDS.values())
        if len(requested) > 256:
            raise SafetyError("Capture is limited to 256 explicit DIDs per run")
        async with self._operation_lock:
            snapshot = self._snapshot(ecu, {}, {})
            snapshot["requested_dids"] = [did_hex(did) for did in sorted(requested)]
            snapshot["complete"] = False
            path = self.store.save("snapshots", snapshot)
            try:
                for did in sorted(requested):
                    try:
                        data = await self._read(ecu, did)
                        record = {"ok": True, "hex": data.hex().upper(), "length": len(data)}
                        snapshot["records"][did_hex(did)] = record
                        for name, identity_did in IDENTITY_DIDS.items():
                            if did == identity_did:
                                snapshot["identity"][name] = data.hex().upper()
                                record["display"] = data.decode("ascii", errors="replace").rstrip("\x00 ")
                    except WorkbenchError as exc:
                        snapshot["records"][did_hex(did)] = {"ok": False, "error": str(exc)}
                        # A missing DID is normal. A broken command stream must not be reused.
                        if (not isinstance(exc, (NegativeResponse, NoData))
                                or isinstance(exc, NegativeResponse) and exc.code in (0x21, 0x78)):
                            snapshot["stopped_reason"] = str(exc)
                            break
                    finally:
                        self.store.update(path, snapshot)
                    if self.link.kind != "demo":
                        await asyncio.sleep(0.35)
                else:
                    snapshot["complete"] = True
                if snapshot["complete"] and not any(record.get("ok") for record in snapshot["records"].values()):
                    snapshot["complete"] = False
                    snapshot["stopped_reason"] = "No requested data record could be read from this ECU"
                if include_dtc and snapshot["complete"]:
                    request = bytes.fromhex("1902FF")
                    try:
                        raw = await self.link.request(ecu, request)
                        data = uds_data(raw, request)
                        if len(data) < 2 or data[0] != 2 or (len(data) - 2) % 4:
                            raise ProtocolError("Malformed DTC response")
                        snapshot["dtcs"] = {"ok": True, "raw": raw.hex().upper(),
                                            "status_availability_mask": data[1],
                                            "items": [{"code": data[i:i + 3].hex().upper(), "status": data[i + 3]}
                                                      for i in range(2, len(data), 4)]}
                    except WorkbenchError as exc:
                        snapshot["dtcs"] = {"ok": False, "error": str(exc)}
                try:
                    snapshot["adapter_voltage"] = await self.link.voltage() if snapshot["complete"] else None
                except WorkbenchError as exc:
                    snapshot["voltage_error"] = str(exc)
                snapshot["finished_at"] = now()
            finally:
                self.store.update(path, snapshot)
            return path

    @staticmethod
    def edit_value(before: bytes, *, value_hex: str | None = None, byte: int | None = None,
                   bit: int | None = None, value: int | None = None) -> bytes:
        if value_hex is not None:
            if any(item is not None for item in (byte, bit, value)):
                raise SafetyError("Choose a full hex value OR one byte/bit edit")
            try:
                after = parse_hex(value_hex)
            except ValueError as exc:
                raise SafetyError(str(exc)) from exc
            if len(after) != len(before):
                raise SafetyError("The record length cannot change")
            return after
        if type(byte) is not int or not 0 <= byte < len(before) or type(value) is not int:
            raise SafetyError("A valid zero-based byte offset and integer value are required")
        after = bytearray(before)
        if bit is None:
            if not 0 <= value <= 255:
                raise SafetyError("Byte value must be 0..255")
            after[byte] = value
        else:
            if type(bit) is not int or not 0 <= bit <= 7 or value not in (0, 1):
                raise SafetyError("Bit index must be 0..7 and bit value must be 0 or 1")
            after[byte] = (after[byte] & ~(1 << bit)) | (value << bit)
        return bytes(after)

    @staticmethod
    def _changes(before: bytes, after: bytes) -> list[dict]:
        return [{"offset": i, "before": f"{a:02X}", "after": f"{b:02X}", "xor": f"{a ^ b:02X}",
                 "bits": [bit for bit in range(8) if (a ^ b) & (1 << bit)]}
                for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]

    def _build_plan(self, snapshot: dict, entry: dict | None, did: int, before: bytes, after: bytes,
                    snapshot_path: str, action: str = "write") -> dict:
        if before == after:
            raise SafetyError("This edit changes no bytes")
        ecu = self._ecu(snapshot["ecu"])
        reasons = []
        kind = snapshot.get("origin", {}).get("kind")
        if kind not in ("demo", "ble"):
            reasons.append("Unknown capture origin")
        if entry is None:
            reasons.append("No reviewed writable dictionary entry selected")
        else:
            try:
                self.dictionary.check_write(entry, snapshot.get("identity", {}), ecu, kind, before, after)
            except (WorkbenchError, ValueError) as exc:
                reasons.append(str(exc))
        limit = 4095 if kind == "demo" else 7
        if len(after) + 3 > limit:
            reasons.append(f"ELM CAF1 supports at most {limit} UDS request bytes; this write needs {len(after) + 3}. "
                           "Long coding transmission needs a separately validated ISO-TP sender")
        nonce = uuid.uuid4().hex[:10].upper()
        return {"schema": "audi-workbench.plan/v1", "created_at": now(), "action": action,
                "origin": {"kind": kind}, "ecu": ecu.as_dict(), "identity": copy.deepcopy(snapshot.get("identity", {})),
                "entry_id": entry["id"] if entry else None, "entry_sha256": digest(entry) if entry else None,
                "did": did_hex(did), "before_hex": before.hex().upper(), "after_hex": after.hex().upper(),
                "changes": self._changes(before, after), "eligible": not reasons, "blocked_reasons": reasons,
                "snapshot_path": snapshot_path, "snapshot_sha256": snapshot.get("sha256"),
                "confirmation": f"{'RESTORE' if action == 'restore' else 'WRITE'} {ecu.key} {did_hex(did)} {nonce}"}

    def make_plan(self, snapshot_path: Path | str, entry_id: str | None = None, did: int | None = None,
                  value_hex: str | None = None, byte: int | None = None, bit: int | None = None,
                  value: int | None = None) -> Path:
        snapshot = self.store.read(snapshot_path)
        if snapshot.get("schema") != "audi-workbench.snapshot/v1":
            raise SafetyError("Select a snapshot artifact")
        entry = self.dictionary.entry(entry_id) if entry_id else None
        if entry:
            entry_did = int(entry["did"], 16)
            if did is not None and did != entry_did:
                raise SafetyError("Requested DID differs from the selected dictionary entry")
            did = entry_did
        if did is None:
            raise SafetyError("Select a dictionary entry or an explicit DID")
        record = snapshot["records"].get(did_hex(did), {})
        if record.get("ok") is not True:
            raise SafetyError("This DID was not successfully read in the snapshot")
        before = parse_hex(record["hex"])
        after = self.edit_value(before, value_hex=value_hex, byte=byte, bit=bit, value=value)
        plan = self._build_plan(snapshot, entry, did, before, after, str(Path(snapshot_path).resolve()))
        return self.store.save("plans", plan)

    @staticmethod
    def _check_identity(expected: dict, current: dict, kind: str):
        for key in REQUIRED_IDENTITY:
            if not expected.get(key) or current.get(key) != expected[key]:
                raise SafetyError(f"ECU identity missing or changed: {key}")
        for key, raw in expected.items():
            if current.get(key) != raw:
                raise SafetyError(f"ECU identity changed: {key}")
        if kind == "ble":
            try:
                vin = parse_hex(current["vin"]).decode("ascii").rstrip("\x00 ")
            except (ValueError, UnicodeError) as exc:
                raise SafetyError("Invalid VIN response") from exc
            if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
                raise SafetyError("A valid 17-character vehicle VIN is required for writes")

    async def _conditions(self) -> dict:
        voltage = await self.link.voltage()
        if not isinstance(voltage, (int, float)) or not math.isfinite(voltage) or not 12.3 <= voltage <= 15.0:
            raise SafetyError(f"Adapter voltage {voltage!r} V is outside the write range 12.3..15.0 V")
        ecu = ECUS["01"]
        speed = await self.link.request(ecu, bytes.fromhex("010D"))
        rpm = await self.link.request(ecu, bytes.fromhex("010C"))
        if len(speed) != 3 or speed[:2] != bytes.fromhex("410D"):
            raise SafetyError("Cannot verify vehicle speed from OBD PID 0D")
        if len(rpm) != 4 or rpm[:2] != bytes.fromhex("410C"):
            raise SafetyError("Cannot verify engine speed from OBD PID 0C")
        speed_value, rpm_value = speed[2], int.from_bytes(rpm[2:4], "big") / 4
        if speed_value != 0 or rpm_value != 0:
            raise SafetyError("Write requires vehicle speed 0 and engine RPM 0 (ignition on, engine off)")
        return {"voltage": voltage, "speed_kph": speed_value, "rpm": rpm_value, "checked_at": now()}

    def _unresolved(self, ecu: ECU, identity: dict, kind: str):
        for path in self.store.files("transactions"):
            tx = self.store.read(path)
            if (tx.get("status") in ("attempting", "unknown", "verification_failed")
                    and tx.get("ecu", {}).get("key") == ecu.key
                    and tx.get("origin", {}).get("kind") == kind
                    and tx.get("identity", {}).get("vin") == identity.get("vin")):
                raise SafetyError(f"Unresolved earlier write: {path}. Run reconcile before another write")

    async def apply(self, plan_path: Path | str, confirmation: str, enable_writes: bool = False) -> Path:
        self._online()
        async with self._operation_lock:
            plan = self.store.read(plan_path)
            if plan.get("schema") != "audi-workbench.plan/v1":
                raise SafetyError("Select a change plan")
            if not enable_writes:
                raise SafetyError("Writes are disabled. Review the plan and explicitly enable this write")
            if not isinstance(confirmation, str) or not hmac.compare_digest(confirmation, plan.get("confirmation", "")):
                raise SafetyError("Confirmation does not exactly match the plan")
            if plan.get("eligible") is not True or plan.get("blocked_reasons"):
                raise SafetyError("Plan is blocked: " + "; ".join(plan.get("blocked_reasons", [])))
            ecu = self._ecu(plan["ecu"])
            if plan.get("origin", {}).get("kind") != self.link.kind:
                raise SafetyError("Demo and real-vehicle artifacts cannot be interchanged")
            entry = self.dictionary.entry(plan["entry_id"])
            if plan["entry_sha256"] != digest(entry) or plan["did"] != entry["did"]:
                raise SafetyError("Dictionary definition changed; create a fresh plan")
            before, after = parse_hex(plan["before_hex"]), parse_hex(plan["after_hex"])
            if before == after:
                raise SafetyError("No-op writes are blocked")
            self.dictionary.check_write(entry, plan["identity"], ecu, self.link.kind, before, after)
            request = b"\x2e" + int(plan["did"], 16).to_bytes(2, "big") + after
            if len(request) > self.link.max_request_bytes:
                raise SafetyError("Write exceeds adapter transport limit; no CAN write was sent")
            self._unresolved(ecu, plan["identity"], self.link.kind)
            conditions = await self._conditions()
            identity, records = await self._identity(ecu)
            self._check_identity(plan["identity"], identity, self.link.kind)
            did = int(plan["did"], 16)
            current = await self._read(ecu, did)
            if current != before:
                raise SafetyError("Stale plan: ECU bytes changed since the snapshot; capture and plan again")
            records[plan["did"]] = {"ok": True, "hex": current.hex().upper(), "length": len(current)}
            backup = self._snapshot(ecu, identity, records)
            backup["purpose"] = "Immediate pre-write configuration backup"
            backup["complete"] = True
            backup_path = self.store.save("snapshots", backup)
            backup_receipt = self.store.read(backup_path)  # Confirm readable, hashed bytes before mutation.
            tx = {"schema": "audi-workbench.transaction/v1", "created_at": now(), "status": "prepared",
                  "origin": {"kind": self.link.kind}, "ecu": ecu.as_dict(), "identity": identity,
                  "entry_id": entry["id"], "entry_sha256": digest(entry), "did": plan["did"],
                  "before_hex": before.hex().upper(), "after_hex": after.hex().upper(),
                  "plan_path": str(Path(plan_path).resolve()), "plan_sha256": plan["sha256"],
                  "backup_path": str(backup_path), "backup_sha256": backup_receipt["sha256"],
                  "conditions": conditions, "trace_file": str(self.store.trace_path)}
            tx_path = self.store.save("transactions", tx)
            try:
                if entry.get("session") == 3:
                    session = await self.link.request(ecu, bytes.fromhex("1003"))
                    if len(session) not in (2, 6) or session[:2] != bytes.fromhex("5003"):
                        if session[:1] == b"\x7f":
                            uds_data(session, bytes.fromhex("1003"))
                        raise ProtocolError("Extended diagnostic session was not confirmed")
                # A session switch can change access/values; verify again immediately before sending.
                conditions = await self._conditions()
                if await self._read(ecu, did) != before:
                    raise SafetyError("Value changed during preflight; no write sent")
                tx.update(status="attempting", attempted_at=now(), final_conditions=conditions)
                self.store.update(tx_path, tx)  # Durable intent before the one and only write call.
            except BaseException as exc:
                tx.update(status="not_sent", error=str(exc), finished_at=now())
                self.store.update(tx_path, tx)
                raise
            acknowledged = False
            try:
                response = await self.link.request(ecu, request)
                tx["write_response_hex"] = response.hex().upper()
                uds_data(response, request)
                acknowledged = True
                actual = await self._read(ecu, did)
                tx["actual_hex"] = actual.hex().upper()
                tx["status"] = "verified" if actual == after else "verification_failed"
                if actual != after:
                    tx["error"] = "Write acknowledged but readback differs. No automatic rollback was attempted"
            except NegativeResponse as exc:
                tx["status"] = "unknown" if acknowledged or exc.code in (0x72, 0x78) else "rejected"
                tx["error"] = str(exc)
            except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - Every post-send failure must be journaled as unknown.
                tx["status"] = "unknown"
                tx["error"] = f"{type(exc).__name__}: {exc}. Do not retry; reconnect and reconcile this transaction"
            tx["finished_at"] = now()
            try:
                self.store.update(tx_path, tx)
            except OSError as exc:
                raise SafetyError(f"Write may have happened but final receipt could not be saved. "
                                  f"Reconcile durable transaction {tx_path}") from exc
            return tx_path

    async def reconcile(self, transaction_path: Path | str) -> Path:
        """Read-only resolution after a timeout/crash; never resends or rolls back."""
        self._online()
        async with self._operation_lock:
            tx = self.store.read(transaction_path)
            if tx.get("schema") != "audi-workbench.transaction/v1":
                raise SafetyError("Select a transaction receipt")
            if tx.get("origin", {}).get("kind") != self.link.kind:
                raise SafetyError("Transaction origin differs from the current connection")
            ecu = self._ecu(tx["ecu"])
            identity, _ = await self._identity(ecu)
            self._check_identity(tx["identity"], identity, self.link.kind)
            current = (await self._read(ecu, int(tx["did"], 16))).hex().upper()
            previous = tx["status"]
            tx["status"] = ("reconciled_after" if current == tx["after_hex"] else
                            "reconciled_before" if current == tx["before_hex"] else "reconciled_other")
            tx["actual_hex"] = current
            tx.setdefault("reconciliations", []).append({"time": now(), "previous_status": previous,
                                                          "actual_hex": current, "trace_file": str(self.store.trace_path)})
            self.store.update(transaction_path, tx)
            return Path(transaction_path)

    def make_restore(self, transaction_path: Path | str) -> Path:
        tx = self.store.read(transaction_path)
        if tx.get("schema") != "audi-workbench.transaction/v1" or tx.get("status") not in ("verified", "reconciled_after"):
            raise SafetyError("Restore requires a verified write or read-only reconciliation showing the written value")
        backup = self.store.read(tx["backup_path"])
        if backup["sha256"] != tx["backup_sha256"]:
            raise SafetyError("Backup no longer matches the transaction")
        if backup.get("records", {}).get(tx["did"], {}).get("hex") != tx["before_hex"]:
            raise SafetyError("Backup value differs from transaction pre-image")
        self._check_identity(tx["identity"], backup["identity"], tx["origin"]["kind"])
        entry = self.dictionary.entry(tx["entry_id"])
        if digest(entry) != tx["entry_sha256"]:
            raise SafetyError("Dictionary changed since this write; review a new plan")
        # The source is an actual verified readback receipt, not a claim of a new live capture.
        plan = self._build_plan(backup, entry, int(tx["did"], 16), parse_hex(tx["after_hex"]),
                                parse_hex(tx["before_hex"]), tx["backup_path"], action="restore")
        plan["source_transaction"] = str(Path(transaction_path).resolve())
        plan["source_transaction_sha256"] = tx["sha256"]
        return self.store.save("plans", plan)

    @staticmethod
    def diff(before_path: Path | str, after_path: Path | str) -> dict:
        before, after = Store.read(before_path), Store.read(after_path)
        if before.get("schema") != "audi-workbench.snapshot/v1" or after.get("schema") != before["schema"]:
            raise SafetyError("Diff requires two snapshots")
        same_target = (before.get("identity") == after.get("identity") and before.get("ecu") == after.get("ecu")
                       and before.get("origin", {}).get("kind") == after.get("origin", {}).get("kind"))
        rows = []
        for did in sorted(set(before["records"]) | set(after["records"])):
            a, b = before["records"].get(did), after["records"].get(did)
            ah = a.get("hex") if a and a.get("ok") else None
            bh = b.get("hex") if b and b.get("ok") else None
            if ah == bh and a == b:
                continue
            if ah is not None and bh is not None and ah == bh:
                continue
            changes = []
            if ah is not None and bh is not None and len(ah) == len(bh):
                changes = Workbench._changes(parse_hex(ah), parse_hex(bh))
            rows.append({"did": did, "before_hex": ah, "after_hex": bh, "changes": changes,
                         "before_error": a.get("error") if a else "not captured",
                         "after_error": b.get("error") if b else "not captured"})
        return {"same_target": same_target, "warning": None if same_target else "Different ECU identities or origins",
                "rows": rows}
