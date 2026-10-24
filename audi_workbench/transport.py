"""BLE ELM327 transport, deliberately limited to single-frame requests.

Incoming ISO-TP messages may be multi-frame. Outgoing CAF1 requests are limited
to seven UDS bytes: ATAL does not implement a long-request sender. This driver
does not claim hardware validation for any adapter, including iCar Pro 2S.

References: ELM327DSJ (elmelectronics.com), Bleak client/scanner documentation.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import math
import re
from typing import Any

from .model import ECU, NegativeResponse, NoData, ProtocolError, Trace, TransportError

MAX_RESPONSE_BYTES = 256 * 1024
BLE_CLEANUP_TIMEOUT = 2.0  # Separate budget for each cancellable backend cleanup call.
GATT_PROFILES = (
    ("Vgate 18F0", "18f0", "2af1", "2af0"),
    ("ELM FFF0", "fff0", "fff2", "fff1"),
    ("ELM FFE0", "ffe0", "ffe1", "ffe1"),
    (
        "Vgate proprietary UART",
        "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
        "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
        "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
    ),
)
_ADAPTER_FAILURES = (
    "BUFFER FULL", "CAN ERROR", "BUS ERROR", "BUS BUSY", "DATA ERROR",
    "RX ERROR", "FB ERROR", "LV RESET", "UNABLE TO CONNECT", "STOPPED",
    "ERROR", "?",
)


def _uuid(value: str) -> str:
    value = value.lower()
    return f"0000{value}-0000-1000-8000-00805f9b34fb" if len(value) == 4 else value


def select_gatt(services: Any) -> tuple[str, Any, Any]:
    """Only known UART pairs with the required advertised properties qualify.

    Adapters may expose more than one known profile; the first profile in
    GATT_PROFILES order wins, but it must match exactly one pair.
    """
    for label, service_uuid, write_uuid, notify_uuid in GATT_PROFILES:
        matches = []
        for service in services:
            if _uuid(service.uuid) != _uuid(service_uuid):
                continue
            writable = [c for c in service.characteristics
                        if _uuid(c.uuid) == _uuid(write_uuid)
                        and {"write", "write-without-response"}.intersection(c.properties)]
            notifying = [c for c in service.characteristics
                         if _uuid(c.uuid) == _uuid(notify_uuid)
                         and {"notify", "indicate"}.intersection(c.properties)]
            matches.extend((label, write, notify) for write in writable for notify in notifying)
        if len(matches) == 1:
            return matches[0]
        if matches:
            break
    raise TransportError(
        "GATT UART is unknown or ambiguous. The discovery trace contains the services; "
        "no arbitrary characteristic was selected."
    )


def _emit(trace: Trace | None, event: str, **fields: Any) -> None:
    if trace:
        trace({"time": dt.datetime.now(dt.UTC).isoformat(), "event": event, **fields})


def _lines(text: str, command: str | None = None) -> list[str]:
    # A prompt is an interpreter boundary, never diagnostic data.
    text = text.strip()
    text = text.removesuffix(">")
    result = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    if command and result and result[0].replace(" ", "").upper() == command.replace(" ", "").upper():
        result.pop(0)
    return result


def parse_elm_response(text: str, expected_rx: int, request: bytes | None = None) -> bytes:
    """Decode ATH1/ATD0 raw CAN output; reject missing or unrelated frames.

    More than one ISO-TP message is permitted only for NRC 78 responses followed
    by one final response. Pending alone is not reported as completion.
    """
    rows = _lines(text, request.hex() if request else None)
    if len(rows) == 1 and rows[0].upper() == "NO DATA":
        raise NoData("ECU returned NO DATA; no complete response was received")
    if any(row.upper() == "NO DATA" for row in rows):
        raise ProtocolError("NO DATA mixed with other response lines; ELM stream is ambiguous")
    messages: list[bytes] = []
    current: bytearray | None = None
    required = 0
    sequence = 1
    for line in rows:
        upper = line.upper()
        if any(error in upper for error in _ADAPTER_FAILURES):
            raise ProtocolError(f"ELM adapter failure: {line}")
        compact = "".join(line.split())
        if not re.fullmatch(r"[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{2}){1,8}", compact):
            raise ProtocolError(f"Unexpected ELM response line: {line!r}")
        address = int(compact[:3], 16)
        if address != expected_rx:
            raise ProtocolError(f"Wrong CAN response ID {address:03X}; expected {expected_rx:03X}")
        frame = bytes.fromhex(compact[3:])
        frame_type = frame[0] >> 4
        if frame_type == 0:
            length = frame[0] & 0x0F
            if current is not None or length == 0 or length > 7 or len(frame) < 1 + length:
                raise ProtocolError("Invalid or interleaved ISO-TP single frame")
            messages.append(frame[1:1 + length])
        elif frame_type == 1:
            if current is not None or len(frame) != 8:
                raise ProtocolError("Invalid or interleaved ISO-TP first frame")
            required = ((frame[0] & 0x0F) << 8) | frame[1]
            if required < 8:
                raise ProtocolError("Invalid ISO-TP first-frame length")
            current = bytearray(frame[2:])
            sequence = 1
        elif frame_type == 2:
            if current is None or (frame[0] & 0x0F) != sequence or len(frame) < 2:
                raise ProtocolError("Unexpected ISO-TP consecutive frame or sequence number")
            current.extend(frame[1:])
            sequence = (sequence + 1) & 0x0F
            if len(current) >= required:
                messages.append(bytes(current[:required]))
                current = None
            elif len(frame) != 8:
                raise ProtocolError("Short non-final ISO-TP consecutive frame")
        else:
            raise ProtocolError("Unexpected ISO-TP flow-control or unsupported frame")
    if current is not None:
        raise ProtocolError(f"Truncated ISO-TP message: received {len(current)} of {required} bytes")
    if not messages:
        raise ProtocolError("No ISO-TP message in ELM response")
    service = request[0] if request else None
    while len(messages) > 1:
        pending = messages.pop(0)
        if len(pending) != 3 or pending[0] != 0x7F or pending[2] != 0x78:
            raise ProtocolError("More than one non-pending response for a single request")
        if service is None:
            service = pending[1]
        if pending[1] != service:
            raise ProtocolError("Response-pending service does not match the request")
    result = messages[0]
    if result[:1] == b"\x7f":
        if len(result) != 3 or (service is not None and result[1] != service):
            raise ProtocolError("Malformed or unrelated negative response")
        if result[2] == 0x78:
            raise NegativeResponse(result[1], result[2])
    elif service is not None and result[0] != service + 0x40:
        raise ProtocolError("Positive response service does not match the request")
    if request and result[0] != 0x7F:
        if request[0] in (0x22, 0x2E) and result[1:3] != request[1:3]:
            raise ProtocolError("Positive response DID does not match the request")
        if request[0] in (0x01, 0x10, 0x19, 0x3E) and result[1:2] != request[1:2]:
            raise ProtocolError("Positive response subfunction/PID does not match the request")
    return result


class _PromptChannel:
    """One ASCII command at a time; a lost boundary poisons this connection."""

    def __init__(self, client: Any, writable: Any, trace: Trace | None = None):
        self.client = client
        self.writable = writable
        self.trace = trace
        self.lock = asyncio.Lock()
        self.poisoned: str | None = None
        self.pending: asyncio.Future[str] | None = None
        self.buffer = bytearray()
        self._terminator_sent = False
        self._started = False

    def poison(self, reason: str) -> None:
        self.poisoned = reason
        if self.pending is not None and not self.pending.done():
            self.pending.set_exception(TransportError(reason))

    def receive(self, _characteristic: Any, data: bytearray) -> None:
        if self.poisoned:
            return
        try:
            _emit(self.trace, "ble_rx", raw_hex=bytes(data).hex().upper(),
                  text=bytes(data).decode("ascii", errors="backslashreplace"))
            if self.pending is None or self.pending.done():
                if not bytes(data).strip() or not self._started:
                    return  # Trailing CR/LF, or a power-on banner/prompt before the first command.
                self.poison("Unsolicited or late BLE data; reconnect before another command")
                return
            self.buffer.extend(data)
            if len(self.buffer) > MAX_RESPONSE_BYTES:
                self.poison("ELM response exceeded the configured memory bound")
                return
            boundary = self.buffer.find(b">")
            if boundary >= 0:
                if not self._terminator_sent or bytes(self.buffer[boundary + 1:]).strip():
                    self.poison("Unexpected bytes after/around the ELM prompt; stream is ambiguous")
                    return
                self.pending.set_result(bytes(self.buffer[:boundary + 1]).decode("ascii"))
        except Exception as exc:  # noqa: BLE001 - Callback/logging failure must poison the pending command.
            self.poison(f"Cannot safely process/log BLE response: {exc}")

    async def command(self, command: str, timeout: float = 8.0) -> str:
        if not command or any(ord(c) < 32 or ord(c) > 126 for c in command):
            raise TransportError("ELM command must be nonempty single-line ASCII; an empty CR repeats a command")
        if len(command) > 128 or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise TransportError("Invalid ELM command size or timeout")
        async with self.lock:
            if self.poisoned:
                raise TransportError(f"ELM stream is unusable: {self.poisoned}")
            if not self.client.is_connected:
                raise TransportError("Bluetooth adapter is disconnected")
            response = "write" in self.writable.properties
            capacity = 20 if response else min(20, self.writable.max_write_without_response_size)
            if capacity < 1:
                raise TransportError("Invalid BLE write capacity")
            payload = (command + "\r").encode("ascii")
            _emit(self.trace, "elm_tx", command=command, raw_hex=payload.hex().upper())
            self.buffer.clear()
            self._started = True
            self.pending = asyncio.get_running_loop().create_future()
            self._terminator_sent = False
            try:
                async with asyncio.timeout(timeout):
                    for offset in range(0, len(payload), capacity):
                        chunk = payload[offset:offset + capacity]
                        self._terminator_sent = offset + capacity >= len(payload)
                        await self.client.write_gatt_char(self.writable, chunk, response=response)
                    answer = await self.pending
                    if self.poisoned:
                        raise TransportError(self.poisoned)
                _emit(self.trace, "elm_rx", command=command, text=answer,
                      raw_hex=answer.encode("ascii").hex().upper())
                return answer
            except TimeoutError as exc:
                self.poisoned = "Timeout waiting for complete ELM command; reconnect, never retry a write"
                _emit(self.trace, "elm_timeout", command=command, partial_hex=self.buffer.hex().upper())
                raise TransportError(self.poisoned) from exc
            except BaseException as exc:
                self.poisoned = "ELM command interrupted or failed; reconnect before another command"
                if isinstance(exc, Exception) and not isinstance(exc, TransportError):
                    raise TransportError(f"ELM command failed: {exc}; reconnect before continuing") from exc
                raise
            finally:
                if self.pending is not None and not self.pending.done():
                    self.pending.cancel()
                self.pending = None


def _bleak() -> tuple[Any, Any]:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise TransportError("BLE support is not installed. Run: uv sync --locked") from exc
    return BleakClient, BleakScanner


async def _discover(timeout: float) -> list[tuple[Any, Any]]:
    if not 0 < timeout <= 60:
        raise TransportError("BLE scan timeout must be in (0, 60] seconds")
    _, scanner = _bleak()
    try:
        found = await scanner.discover(timeout=timeout, return_adv=True)
        return list(found.values())
    except Exception as exc:
        raise TransportError(
            f"BLE scan failed: {exc}. On macOS check Privacy & Security > Bluetooth for your terminal."
        ) from exc


async def scan_devices(timeout: float = 8.0) -> list[dict[str, Any]]:
    """Named devices first, strongest signal first: the OBD adapter is usually the closest."""
    devices = [
        {"address": device.address, "name": adv.local_name or device.name or "",
         "service_uuids": list(adv.service_uuids or []), "rssi": adv.rssi}
        for device, adv in await _discover(timeout)
    ]
    return sorted(devices, key=lambda d: (not d["name"], -(d["rssi"] if d["rssi"] is not None else -999)))


class BLELink:
    kind = "ble"
    max_request_bytes = 7

    def __init__(self, device: str, trace: Trace | None = None):
        self.device = device
        self.trace = trace
        self.metadata: dict[str, Any] = {
            "driver": "ble-elm327-caf1", "hardware_validated": False,
            "max_request_bytes": self.max_request_bytes, "long_request_support": False,
        }
        self._client: Any = None
        self._notify: Any = None
        self._channel: _PromptChannel | None = None
        self._lock = asyncio.Lock()
        self._selected: tuple[int, int] | None = None
        self._ready = False
        self._closing = False

    def _disconnected(self, _client: Any) -> None:
        self._ready = False
        if self._channel is not None and not self._closing:
            self._channel.poison("Bluetooth disconnected; operation result may be unknown")

    async def open(self) -> None:
        async with self._lock:
            if self._ready:
                return
            if not self.device or not self.device.strip():
                raise TransportError("Choose an exact BLE name or address from the scan command")
            client_class, _ = _bleak()
            discovered = await _discover(8)
            wanted = self.device.casefold()
            candidates = [(d, a) for d, a in discovered
                          if wanted in {d.address.casefold(), (a.local_name or d.name or "").casefold()}]
            if len(candidates) != 1:
                names = ", ".join(f"{a.local_name or d.name or '(unnamed)'} [{d.address}]"
                                  for d, a in discovered)
                raise TransportError(f"BLE selection must match exactly one device; found {len(candidates)}. {names}")
            device, advertisement = candidates[0]
            self.metadata.update({"device_name": advertisement.local_name or device.name,
                                  "device_address": device.address})
            self._client = client_class(device, disconnected_callback=self._disconnected, timeout=20)
            self._closing = False
            try:
                await self._client.connect()
                services = list(self._client.services)
                self.metadata["gatt"] = [
                    {"uuid": service.uuid, "characteristics": [
                        {"uuid": c.uuid, "properties": list(c.properties)} for c in service.characteristics
                    ]} for service in services
                ]
                _emit(self.trace, "gatt_discovery", **self.metadata)
                label, writable, self._notify = select_gatt(services)
                self.metadata.update({"uart_profile": label, "write_uuid": writable.uuid,
                                      "notify_uuid": self._notify.uuid})
                self._channel = _PromptChannel(self._client, writable, self.trace)
                await self._client.start_notify(self._notify, self._channel.receive)
                await self._initialize()
                self._ready = True
                _emit(self.trace, "adapter_ready", **self.metadata)
            except BaseException as exc:
                await self._close_after_failure()
                if isinstance(exc, Exception) and not isinstance(exc, TransportError):
                    raise TransportError(f"BLE connection or adapter initialization failed: {exc}") from exc
                raise

    async def _initialize(self) -> None:
        assert self._channel is not None
        identity = _lines(await self._channel.command("ATI"), "ATI")
        identity_text = " ".join(identity)
        if not identity_text or not any(s in identity_text.upper() for s in ("ELM", "STN", "VGATE", "VLINK")):
            raise TransportError(f"ATI did not identify an ELM-compatible adapter: {identity_text!r}")
        self.metadata["interpreter"] = identity_text
        # ATD restores interpreter settings left by another app; it is not an ECU reset.
        for command in ("ATD", "ATE0", "ATL0", "ATS1", "ATH1", "ATD0", "ATR1", "ATAL", "ATSP6",
                        "ATCFC1", "ATCAF1", "ATAT0", "ATSTFF"):
            await self._at(command)
        self.metadata["required_commands_accepted"] = True

    async def _at(self, command: str) -> None:
        assert self._channel is not None
        if _lines(await self._channel.command(command), command) != ["OK"]:
            self._channel.poison(f"Required ELM command {command} was not accepted")
            raise TransportError(f"Adapter does not support required command {command}; operation stopped")

    def _ensure_ready(self) -> _PromptChannel:
        if not self._ready or self._channel is None or self._channel.poisoned:
            raise TransportError("Adapter is not ready; explicitly reconnect before continuing")
        return self._channel

    async def _select(self, ecu: ECU) -> None:
        if self._selected == (ecu.tx, ecu.rx):
            return
        # Set both destination and payload before selecting user-defined FC mode.
        for command in (f"ATSH{ecu.tx:03X}", f"ATCRA{ecu.rx:03X}", f"ATFCSH{ecu.tx:03X}",
                        "ATFCSD300000", "ATFCSM1"):
            await self._at(command)
        self._selected = (ecu.tx, ecu.rx)

    async def request(self, ecu: ECU, payload: bytes, *, timeout: float = 8.0) -> bytes:
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= self.max_request_bytes:
            raise TransportError(
                "ELM CAF1 supports 1–7 outgoing UDS bytes only (SID + DID + at most four value bytes). "
                "Long coding writes/restores need a separate validated ISO-TP sender; nothing was sent."
            )
        async with self._lock:
            channel = self._ensure_ready()
            try:
                await self._select(ecu)
                _emit(self.trace, "diagnostic_tx", ecu=ecu.key, tx=f"{ecu.tx:03X}",
                      rx=f"{ecu.rx:03X}", payload=payload.hex().upper())
                response = await channel.command(payload.hex().upper(), timeout)
                result = parse_elm_response(response, ecu.rx, payload)
                _emit(self.trace, "diagnostic_rx", ecu=ecu.key, payload=result.hex().upper())
                return result
            except NegativeResponse:
                # Pending with a returned ELM prompt may be followed by late data.
                channel.poison("Final response missing after NRC 78; operation result unknown")
                await self._close_after_failure()
                raise
            except ProtocolError:
                channel.poison("Diagnostic response framing/identity is invalid")
                await self._close_after_failure()
                raise
            except BaseException:
                if channel.poisoned:
                    await self._close_after_failure()
                raise

    async def voltage(self) -> float:
        async with self._lock:
            channel = self._ensure_ready()
            try:
                rows = _lines(await channel.command("ATRV"), "ATRV")
                if len(rows) != 1 or not re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?V", rows[0], re.IGNORECASE):
                    raise ProtocolError("Adapter returned an invalid voltage value")
                voltage = float(rows[0][:-1])
                if not 0 <= voltage <= 30:
                    raise ProtocolError("Adapter voltage is outside a plausible range")
                return voltage
            except BaseException:
                if channel.poisoned:
                    await self._close_after_failure()
                raise

    async def _close_after_failure(self) -> None:
        # The caller must re-raise its original request error/cancellation, even
        # when a second cancellation interrupts one of the cleanup attempts.
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await self.close()

    async def close(self) -> None:
        self._ready = False
        self._selected = None
        self._closing = True
        client, notify = self._client, self._notify
        if self._channel:
            self._channel.poison("Connection closed")
        try:
            if client is not None:
                try:
                    if notify is not None and client.is_connected:
                        with contextlib.suppress(Exception):
                            async with asyncio.timeout(BLE_CLEANUP_TIMEOUT):
                                await client.stop_notify(notify)
                finally:
                    # CoreBluetooth may never finish stop_notify. Disconnect
                    # gets its own attempt and deadline, including on cancellation.
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(BLE_CLEANUP_TIMEOUT):
                            await client.disconnect()
        finally:
            # Keep the backend reachable until both cleanup attempts have run.
            if self._client is client:
                self._client = None
                self._notify = None
