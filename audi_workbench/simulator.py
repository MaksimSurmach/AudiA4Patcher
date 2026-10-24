"""Deterministic, persistent synthetic ECU. Never connects to vehicle hardware."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from .model import ECU, ECUS, IDENTITY_DIDS, Trace, TransportError
from .store import atomic_write

DEMO_IDENTITY = {
    "vin": b"DEMO0000000000001".hex().upper(),
    "part_number": b"DEMO-5F-PART".hex().upper(),
    "sw_version": b"SIM-0001".hex().upper(),
    "hw_part_number": b"DEMO-5F-HW".hex().upper(),
    "hw_version": b"H00".hex().upper(),
    "system_name": b"DEMO-MHS2-SIMULATOR".hex().upper(),
    "asam_name": b"EV_DEMO_5F_ONLY".hex().upper(),
    "asam_version": b"000001".hex().upper(),
}
STATE_SCHEMA = "audi-workbench.demo-state/v1"


def _initial_state() -> dict[str, Any]:
    ecus = {}
    for key in ECUS:
        identity = deepcopy(DEMO_IDENTITY)
        if key != "5F":
            identity["part_number"] = f"DEMO-{key}-PART".encode().hex().upper()
            identity["hw_part_number"] = f"DEMO-{key}-HW".encode().hex().upper()
            identity["asam_name"] = f"EV_DEMO_{key}_ONLY".encode().hex().upper()
        records = {f"{did:04X}": identity[name] for name, did in IDENTITY_DIDS.items()}
        if key == "5F":
            records.update({"0600": "00" * 20, "D100": "01050000", "22AD": "112233445566"})
        ecus[key] = {"records": records}
    return {"schema": STATE_SCHEMA, "ecus": ecus}


def _validate_state(state: Any) -> None:
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
        raise ValueError("unrecognized schema")
    if not isinstance(state.get("ecus"), dict) or set(state["ecus"]) != set(ECUS):
        raise ValueError("ECU records are missing")
    expected = _initial_state()["ecus"]
    for key, module in state["ecus"].items():
        records = module["records"]  # Malformed shapes raise TypeError/KeyError for the caller.
        if not set(expected[key]["records"]) <= set(records):
            raise ValueError(f"missing records for ECU {key}")
        for raw in records.values():
            bytes.fromhex(raw)


class DemoLink:
    kind = "demo"
    max_request_bytes = 4095

    def __init__(self, state_path: Path | None = None, trace: Trace | None = None):
        self.state_path = Path(state_path) if state_path is not None else None
        self.trace = trace
        self.metadata = {"simulation": True, "adapter": "Synthetic ECU; no hardware",
                         "hardware_verified": False, "transport": "in-process simulator"}
        self.state = _initial_state()
        self.requests: list[tuple[str, bytes]] = []
        self.writes: list[tuple[str, bytes]] = []
        self.speed_kph = 0
        self.rpm = 0
        self.battery_voltage = 13.0
        self.deny_writes = False
        self.timeout_after_write = False
        self.is_open = False

    async def open(self) -> None:
        if self.is_open:
            return
        if self.state_path is not None and self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                _validate_state(state)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise TransportError(f"Invalid existing simulator state; it was not reset: {exc}") from exc
            self.state = state
        elif self.state_path is not None:
            self._persist()
        self.is_open = True

    async def close(self) -> None:
        self.is_open = False

    def _persist(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write(self.state_path, (json.dumps(self.state, indent=2, sort_keys=True) + "\n").encode())
        except OSError as exc:
            raise TransportError(f"Cannot persist simulator state: {exc}") from exc

    def _event(self, value: dict[str, Any]) -> None:
        if self.trace is not None:
            self.trace({"origin": "demo", **value})

    async def voltage(self) -> float:
        if not self.is_open:
            raise TransportError("Simulator is not open")
        value = float(self.battery_voltage)
        if not math.isfinite(value):
            raise TransportError("Simulator voltage is not finite")
        self._event({"event": "voltage", "value": value})
        return value

    async def request(self, ecu: ECU, payload: bytes, *, timeout: float = 8.0) -> bytes:
        if not self.is_open:
            raise TransportError("Simulator is not open")
        if not payload or len(payload) > self.max_request_bytes:
            raise TransportError("Invalid simulator request length")
        if ecu.key not in ECUS or (ecu.tx, ecu.rx) != (ECUS[ecu.key].tx, ECUS[ecu.key].rx):
            raise TransportError("No simulated ECU at the requested address")
        self.requests.append((ecu.key, bytes(payload)))
        self._event({"event": "diagnostic_tx", "ecu": ecu.key, "tx": ecu.tx,
                     "rx": ecu.rx, "payload": payload.hex().upper()})
        response = self._respond(ecu, payload)
        if payload[0] == 0x2E and response[:1] == b"\x6e" and self.timeout_after_write:
            self._event({"event": "diagnostic_error", "error": "timeout after ECU accepted write"})
            raise TransportError("Simulated timeout after ECU accepted write; result is ambiguous")
        self._event({"event": "diagnostic_rx", "ecu": ecu.key, "payload": response.hex().upper()})
        return response

    def _respond(self, ecu: ECU, payload: bytes) -> bytes:
        service = payload[0]
        negative = lambda code: bytes([0x7F, service, code])
        records = self.state["ecus"][ecu.key]["records"]
        if service == 0x22:
            if len(payload) != 3:
                return negative(0x13)
            value = records.get(payload[1:].hex().upper())
            return negative(0x31) if value is None else b"\x62" + payload[1:] + bytes.fromhex(value)
        if service == 0x2E:
            if len(payload) < 4:
                return negative(0x13)
            if self.deny_writes:
                return negative(0x33)
            did, value = payload[1:3].hex().upper(), payload[3:]
            if ecu.key != "5F" or did not in ("D100", "0600"):
                return negative(0x31)
            if len(value) != len(bytes.fromhex(records[did])):
                return negative(0x13)
            records[did] = value.hex().upper()
            self._persist()
            self.writes.append((ecu.key, bytes(payload)))
            return b"\x6e" + payload[1:3]
        if service == 0x10:
            if len(payload) != 2:
                return negative(0x13)
            if payload[1] not in (1, 3):
                return negative(0x12)
            return b"\x50" + payload[1:] + bytes.fromhex("003201F4")
        if service == 0x3E:
            return b"\x7e\x00" if payload == b"\x3e\x00" else negative(0x12)
        if service == 0x19:
            return b"\x59\x02\xff" if payload == b"\x19\x02\xff" else negative(0x12)
        if service == 0x01 and ecu.key == "01":
            if payload == b"\x01\x0d":
                return b"\x41\x0d" + bytes([max(0, min(255, int(self.speed_kph)))])
            if payload == b"\x01\x0c":
                rpm = max(0, min(0xFFFF, int(float(self.rpm) * 4)))
                return b"\x41\x0c" + rpm.to_bytes(2, "big")
            return negative(0x12)
        return negative(0x11)
