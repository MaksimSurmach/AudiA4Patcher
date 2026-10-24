from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class WorkbenchError(Exception):
    """An expected, user-facing failure."""


class TransportError(WorkbenchError):
    """Link error; never retry a mutation automatically."""


class NoData(TransportError):
    """ELM returned a complete prompt with no ECU response; stream remains aligned."""


class ProtocolError(WorkbenchError):
    """Malformed, incomplete or unrelated diagnostic response."""


class SafetyError(WorkbenchError):
    """A required write precondition was not met."""


class NegativeResponse(ProtocolError):
    def __init__(self, service: int, code: int):
        self.service = service
        self.code = code
        names = {
            0x11: "service not supported", 0x12: "subfunction not supported",
            0x13: "incorrect length/format", 0x21: "busy", 0x22: "conditions not correct",
            0x31: "request out of range", 0x33: "security access denied",
            0x35: "invalid key", 0x36: "too many attempts", 0x37: "delay not expired",
            0x72: "programming failure", 0x78: "response pending; final result unknown",
            0x7E: "subfunction unavailable in session", 0x7F: "service unavailable in session",
        }
        super().__init__(f"UDS {service:02X}: NRC {code:02X} ({names.get(code, 'negative response')})")


@dataclass(frozen=True)
class ECU:
    key: str
    name: str
    tx: int
    rx: int

    def __post_init__(self):
        if not (0 <= self.tx <= 0x7FF and 0 <= self.rx <= 0x7FF) or self.tx == self.rx:
            raise ValueError("Only distinct physical 11-bit CAN IDs are supported")

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "tx": self.tx, "rx": self.rx}


ECUS = {
    "01": ECU("01", "Engine (OBD / identity)", 0x7E0, 0x7E8),
    "19": ECU("19", "CAN Gateway", 0x710, 0x77A),
    "5F": ECU("5F", "Information electronics / MHS2", 0x773, 0x7DD),
}

IDENTITY_DIDS = {
    "vin": 0xF190,
    "part_number": 0xF187,
    "sw_version": 0xF189,
    "hw_part_number": 0xF191,
    "hw_version": 0xF1A3,
    "system_name": 0xF197,
    "asam_name": 0xF19E,
    "asam_version": 0xF1A2,
}

# Every write needs these exact raw identifiers, not a fuzzy model-name match.
REQUIRED_IDENTITY = ("vin", "part_number", "sw_version", "hw_part_number", "asam_name", "asam_version")

Trace = Callable[[dict[str, Any]], None]


class DiagnosticLink(Protocol):
    kind: str
    metadata: dict[str, Any]
    max_request_bytes: int
    trace: Trace | None

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def request(self, ecu: ECU, payload: bytes, *, timeout: float = 8.0) -> bytes: ...
    async def voltage(self) -> float: ...


def parse_hex(value: str, *, allow_empty: bool = False) -> bytes:
    compact = "".join(value.split())
    if len(compact) % 2 or (not compact and not allow_empty):
        raise ValueError("Hex must contain complete bytes, e.g. 0102A0")
    return bytes.fromhex(compact)


def did_hex(did: int) -> str:
    if type(did) is not int or not 0 <= did <= 0xFFFF:
        raise ValueError("DID must be a 16-bit integer")
    return f"{did:04X}"


def uds_data(response: bytes, request: bytes) -> bytes:
    if not request:
        raise ProtocolError("Empty diagnostic request")
    if response[:1] == b"\x7f":
        if len(response) != 3 or response[1] != request[0]:
            raise ProtocolError("Malformed or unrelated negative response")
        raise NegativeResponse(response[1], response[2])
    expected = bytes([request[0] + 0x40]) + request[1:3]
    if request[0] in (0x22, 0x2E):
        if len(response) < 3 or not response.startswith(expected):
            raise ProtocolError("Positive response does not echo the requested service and DID")
        if request[0] == 0x2E and len(response) != 3:
            raise ProtocolError("Unexpected bytes in WriteDataByIdentifier response")
        return response[3:]
    if not response or response[0] != request[0] + 0x40:
        raise ProtocolError("Unexpected diagnostic response service")
    if request[0] == 0x09 and (len(request) < 2 or response[1:2] != request[1:2]):
        raise ProtocolError("Positive response does not echo the requested vehicle information PID")
    return response[1:]
