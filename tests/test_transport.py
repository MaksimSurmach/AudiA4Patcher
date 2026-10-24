"""Protocol tests use captured-style ELM output, never actual vehicle hardware."""
import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from audi_workbench.dictionary import Dictionary
from audi_workbench.model import ECUS, NegativeResponse, NoData, ProtocolError, SafetyError, TransportError
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store
from audi_workbench.transport import BLELink, _PromptChannel, parse_elm_response, select_gatt

MIXED_NO_DATA = (
    "7DD 10 0A 62 F1 90 01 02 03\rNO DATA\r>",
    "7DD 03 7F 22 78 00 00 00 00\rNO DATA\r>",
    "NO DATA\rCAN ERROR\r>",
    "NO DATA\r7DD 04 62 F1 90 57\r>",
    "NO DATA\rNO DATA\r>",
)


class ISOTransportParsingTests(unittest.TestCase):
    def test_only_a_sole_no_data_status_is_recoverable(self):
        for output in ("NO DATA\r>", " 22 f1 90\r\n no data \r\n>"):
            with self.subTest(output=output), self.assertRaises(NoData):
                parse_elm_response(output, 0x7DD, bytes.fromhex("22F190"))

    def test_mixed_no_data_cannot_hide_partial_pending_or_failed_responses(self):
        for output in MIXED_NO_DATA:
            with self.subTest(output=output), self.assertRaises(ProtocolError):
                parse_elm_response("22F190\r" + output, 0x7DD, bytes.fromhex("22F190"))

    def test_declared_length_discards_padding(self):
        self.assertEqual(
            parse_elm_response("7DD 05 62 06 00 A1 B2 AA AA\r>", 0x7DD),
            bytes.fromhex("620600A1B2"),
        )

    def test_truncated_multiframe_is_not_a_successful_read(self):
        with self.assertRaises(ProtocolError):
            parse_elm_response("7DD 10 0A 62 F1 90 57 41 55\r>", 0x7DD)

    def test_multiframe_identifier_response(self):
        self.assertEqual(
            parse_elm_response(
                "7DD 10 0A 62 F1 90 57 41 55\r7DD 21 31 32 33 34 00 00 00\r>",
                0x7DD, bytes.fromhex("22F190"),
            ), bytes.fromhex("62F19057415531323334"),
        )

    def test_compact_output_and_command_echo(self):
        self.assertEqual(
            parse_elm_response("22F190\r7DD0462F19057000000\r>", 0x7DD, bytes.fromhex("22F190")),
            bytes.fromhex("62F19057"),
        )

    def test_sequence_wrap_at_sixteen(self):
        data = bytes(range(130))
        frames = [f"7DD 10 82 {data[:6].hex(' ')}"]
        for i, offset in enumerate(range(6, len(data), 7), 1):
            frame = bytes([0x20 | (i % 16)]) + data[offset:offset + 7]
            frames.append("7DD " + frame.ljust(8, b"\x00").hex(" "))
        self.assertEqual(parse_elm_response("\r".join(frames) + "\r>", 0x7DD), data)

    def test_pending_then_final(self):
        self.assertEqual(
            parse_elm_response("7DD 03 7F 22 78\r7DD 04 62 F1 90 57\r>",
                               0x7DD, bytes.fromhex("22F190")), bytes.fromhex("62F19057"),
        )

    def test_pending_alone_is_not_success(self):
        with self.assertRaises(NegativeResponse) as ctx:
            parse_elm_response("7DD 03 7F 2E 78\r>", 0x7DD, bytes.fromhex("2ED10000"))
        self.assertEqual(ctx.exception.code, 0x78)

    def test_valid_denial_is_preserved(self):
        self.assertEqual(parse_elm_response("7DD 03 7F 2E 33\r>", 0x7DD,
                                            bytes.fromhex("2ED10000")), bytes.fromhex("7F2E33"))

    def test_wrong_id_sid_or_did_never_becomes_a_read(self):
        for text in ("7DE 04 62 F1 90 57", "7DD 04 61 F1 90 57", "7DD 04 62 F1 91 57",
                     "7DD 03 7F 2E 31"):
            with self.subTest(text=text), self.assertRaises(ProtocolError):
                parse_elm_response(text, 0x7DD, bytes.fromhex("22F190"))

    def test_bad_isotp_and_dlc_output_rejected(self):
        for text in (
            "7DD 08 62 F1 90 57 41 55 58",  # SF length > 7.
            "7DD 00", "7DD 30 00 00", "7DD 21 01 02",  # Empty / FC / orphan CF.
            "7DD 8 04 62 F1 90 57 00 00 00",  # DLC must be disabled.
            "7DD 10 0A 62 F1 90 57 41 55\r7DD 22 31 32 33 34",
            "7DD 10 0F 62 F1 90 57 41 55\r7DD 21 31 32",  # Short non-final CF.
            "7DD 10 0A 62 F1 90 57 41 55\r7DD 03 62 06 00",  # Interleaved SF.
            "7DD 03 62 06 00\r7DD 03 62 06 00",  # Duplicate final response.
            "7DD 03 7F 2E 78\r7DD 04 62 F1 90 57",  # Unrelated pending.
        ):
            with self.subTest(text=text), self.assertRaises(ProtocolError):
                parse_elm_response(text, 0x7DD, bytes.fromhex("22F190"))

    def test_adapter_failures_are_not_silently_dropped(self):
        for text in ("BUFFER FULL", "CAN ERROR", "<DATA ERROR", "STOPPED", "?", "SEARCHING..."):
            with self.subTest(text=text), self.assertRaises(ProtocolError):
                parse_elm_response(text + "\r>", 0x7DD)
        with self.assertRaises(TransportError):
            parse_elm_response("NO DATA\r>", 0x7DD)


def characteristic(uuid="2af1", properties=("write",), capacity=20):
    return SimpleNamespace(uuid=uuid, properties=list(properties), max_write_without_response_size=capacity)


class GATTSelectionTests(unittest.TestCase):
    def test_supported_profile_and_properties(self):
        write = characteristic()
        notify = characteristic("2af0", ("notify",))
        svc = SimpleNamespace(uuid="18f0", characteristics=[write, notify])
        self.assertEqual(select_gatt([svc]), ("Vgate 18F0", write, notify))

    def test_same_characteristic_can_write_and_notify(self):
        uart = characteristic("ffe1", ("write-without-response", "notify"))
        self.assertEqual(select_gatt([SimpleNamespace(uuid="ffe0", characteristics=[uart])])[1:],
                         (uart, uart))

    def test_several_known_profiles_pick_the_first_listed(self):
        vgate = SimpleNamespace(uuid="18f0", characteristics=[characteristic(), characteristic("2af0", ("notify",))])
        uart = characteristic("bef8d6c9-9c21-4c9e-b632-bd58c1009f9f", ("write", "notify"))
        proprietary = SimpleNamespace(uuid="e7810a71-73ae-499d-8c15-faa9aef0c3f2", characteristics=[uart])
        self.assertEqual(select_gatt([proprietary, vgate])[0], "Vgate 18F0")
        self.assertEqual(select_gatt([proprietary])[0], "Vgate proprietary UART")

    def test_unknown_or_ambiguous_gatt_is_blocked(self):
        for services in (
            [SimpleNamespace(uuid="1234", characteristics=[characteristic(), characteristic("2af0", ("notify",))])],
            [SimpleNamespace(uuid="18f0", characteristics=[characteristic(), characteristic("2af0", ("read",))])],
            [SimpleNamespace(uuid="18f0", characteristics=[characteristic(), characteristic(),
                                                          characteristic("2af0", ("notify",))])],
        ):
            with self.subTest(services=services), self.assertRaises(TransportError):
                select_gatt(services)


class FakeBLE:
    """A prompt-speaking interpreter driven through real BLE chunk assembly."""
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.is_connected = True
        self.pending = bytearray()
        self.chunks = []
        self.commands = []
        self.receiver = None
        self.fragment = 3
        self.disconnected = False

    async def write_gatt_char(self, characteristic, data, *, response):
        self.chunks.append((bytes(data), response))
        self.pending.extend(data)
        if self.pending.endswith(b"\r"):
            cmd = bytes(self.pending[:-1]).decode("ascii")
            self.pending.clear()
            self.commands.append(cmd)
            answer = self.answers.get(cmd, "ELM327 v2.1\r>" if cmd == "ATI" else "OK\r>")
            if answer is not None:
                data = answer.encode("ascii")
                for offset in range(0, len(data), self.fragment):
                    self.receiver(None, bytearray(data[offset:offset + self.fragment]))

    async def disconnect(self):
        self.disconnected = True
        self.is_connected = False

    async def stop_notify(self, characteristic):
        pass


class CleanupBLE(FakeBLE):
    """Cancellable backend waits controlled by events instead of wall-clock sleeps."""
    def __init__(self, answers=None, *, hang_stop=False, hang_disconnect=False):
        super().__init__(answers)
        self.stop_started = asyncio.Event()
        self.disconnect_started = asyncio.Event()
        self.write_started = asyncio.Event()
        self.stop_release = asyncio.Event()
        self.disconnect_release = asyncio.Event()
        self.cleanup_calls = []
        if not hang_stop:
            self.stop_release.set()
        if not hang_disconnect:
            self.disconnect_release.set()

    async def write_gatt_char(self, characteristic, data, *, response):
        await super().write_gatt_char(characteristic, data, response=response)
        if self.commands and self.commands[-1].startswith("2E"):
            self.write_started.set()

    async def stop_notify(self, characteristic):
        self.cleanup_calls.append("stop_notify")
        self.stop_started.set()
        await self.stop_release.wait()

    async def disconnect(self):
        self.cleanup_calls.append("disconnect")
        self.disconnect_started.set()
        await self.disconnect_release.wait()
        await super().disconnect()

    def release_cleanup(self):
        self.stop_release.set()
        self.disconnect_release.set()


def attach_link(answers=None, *, fake=None):
    fake = FakeBLE(answers) if fake is None else fake
    link = BLELink("test-adapter")
    channel = _PromptChannel(fake, characteristic())
    fake.receiver = channel.receive
    link._client = fake
    link._notify = characteristic("2af0", ("notify",))
    link._channel = channel
    link._ready = True
    return link, fake, channel


class PromptChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_notifications_and_command_chunking(self):
        fake = FakeBLE()
        write = characteristic(properties=("write-without-response",), capacity=7)
        channel = _PromptChannel(fake, write)
        fake.receiver = channel.receive
        self.assertEqual(await channel.command("AT" + "A" * 25), "OK\r>")
        self.assertTrue(all(len(chunk) <= 7 and response is False for chunk, response in fake.chunks))
        self.assertEqual(b"".join(c for c, _ in fake.chunks), b"AT" + b"A" * 25 + b"\r")

    async def test_empty_command_cannot_repeat_previous_write(self):
        _, fake, channel = attach_link()
        for command in ("", "\r", "ATZ\rATZ", "ATZ\n"):
            with self.assertRaises(TransportError):
                await channel.command(command)
        self.assertEqual(fake.commands, [])

    async def test_timeout_poison_blocks_late_data_and_next_request(self):
        _, fake, channel = attach_link({"22F190": None})
        with self.assertRaises(TransportError):
            await channel.command("22F190", timeout=0.01)
        channel.receive(None, bytearray(b"7DD 04 62 F1 90 57\r>"))
        with self.assertRaises(TransportError):
            await channel.command("22F191")
        self.assertEqual(fake.commands, ["22F190"])

    async def test_banner_before_the_first_command_is_ignored(self):
        _, _, channel = attach_link()
        channel.receive(None, bytearray(b"\r\rELM327 v2.3\r\r>"))
        self.assertIsNone(channel.poisoned)
        self.assertEqual(await channel.command("ATI"), "ELM327 v2.1\r>")

    async def test_trailing_whitespace_is_permitted_but_late_payload_is_not(self):
        _, _, channel = attach_link()
        await channel.command("ATI")
        channel.receive(None, bytearray(b"\r\n"))
        self.assertIsNone(channel.poisoned)
        channel.receive(None, bytearray(b"7DD 03 7F 22 78\r>"))
        with self.assertRaises(TransportError):
            await channel.command("ATI")

    async def test_extra_payload_after_prompt_is_ambiguous(self):
        _, _, channel = attach_link({"ATI": "ELM327\r>unexpected"})
        with self.assertRaises(TransportError):
            await channel.command("ATI")

    async def test_extra_notification_after_complete_prompt_also_fails(self):
        _, fake, channel = attach_link({"ATI": "ELM327\r>unexpected"})
        fake.fragment = 8  # First notification ends exactly at the prompt.
        with self.assertRaises(TransportError):
            await channel.command("ATI")

    async def test_interrupted_command_poisoned(self):
        _, fake, channel = attach_link({"22F190": None})
        task = asyncio.create_task(channel.command("22F190"))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(TransportError):
            await channel.command("22F190")
        self.assertEqual(fake.commands, ["22F190"])

    async def test_response_memory_bound(self):
        _, _, channel = attach_link({"ATI": None})
        with patch("audi_workbench.transport.MAX_RESPONSE_BYTES", 16):
            task = asyncio.create_task(channel.command("ATI"))
            await asyncio.sleep(0)
            channel.receive(None, bytearray(b"A" * 17))
            with self.assertRaises(TransportError):
                await task

    async def test_concurrent_commands_do_not_interleave(self):
        _, fake, channel = attach_link()
        result = await asyncio.gather(channel.command("ATI"), channel.command("ATE0"))
        self.assertEqual(result, ["ELM327 v2.1\r>", "OK\r>"])
        self.assertEqual(fake.commands, ["ATI", "ATE0"])


class BLELinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialization_and_complete_read_chain(self):
        link, fake, _ = attach_link({"22F190":
            "7DD 10 0A 62 F1 90 57 41 55\r7DD 21 31 32 33 34 00 00 00\r>"})
        await link._initialize()
        result = await link.request(ECUS["5F"], bytes.fromhex("22F190"))
        self.assertEqual(result, bytes.fromhex("62F19057415531323334"))
        self.assertEqual(fake.commands[-6:], ["ATSH773", "ATCRA7DD", "ATFCSH773", "ATFCSD300000",
                                              "ATFCSM1", "22F190"])
        self.assertTrue(link.metadata["required_commands_accepted"])

    async def test_missing_required_command_aborts_before_diagnostic_data(self):
        link, fake, _ = attach_link({"ATFCSD300000": "?\r>"})
        with self.assertRaises(TransportError):
            await link.request(ECUS["5F"], bytes.fromhex("22F190"))
        self.assertNotIn("22F190", fake.commands)
        self.assertTrue(fake.disconnected)

    async def test_init_command_support_is_not_inferred_from_ati_version(self):
        link, _, _ = attach_link({"ATD0": "?\r>"})
        with self.assertRaises(TransportError):
            await link._initialize()

    async def test_long_write_is_blocked_without_sending_anything(self):
        link, fake, _ = attach_link()
        with self.assertRaisesRegex(TransportError, "seven|1–7"):
            await link.request(ECUS["5F"], bytes.fromhex("2E06000000000000"))
        self.assertEqual(fake.commands, [])

    async def test_single_frame_write_is_never_retried_on_pending(self):
        link, fake, _ = attach_link({"2ED10000": "7DD 03 7F 2E 78\r>"})
        with self.assertRaises(NegativeResponse):
            await link.request(ECUS["5F"], bytes.fromhex("2ED10000"))
        self.assertEqual(fake.commands.count("2ED10000"), 1)
        self.assertTrue(fake.disconnected)
        with self.assertRaises(TransportError):
            await link.request(ECUS["5F"], bytes.fromhex("22D100"))

    async def test_single_frame_write_is_never_retried_on_timeout(self):
        link, fake, _ = attach_link({"2ED10000": None})
        with self.assertRaises(TransportError):
            await link.request(ECUS["5F"], bytes.fromhex("2ED10000"), timeout=0.01)
        self.assertEqual(fake.commands.count("2ED10000"), 1)
        self.assertTrue(fake.disconnected)

    async def test_ble_send_failure_closes_connection_without_retry(self):
        link, fake, _ = attach_link()
        await link._select(ECUS["5F"])
        fake.write_gatt_char = AsyncMock(side_effect=OSError("device lost"))
        with self.assertRaisesRegex(TransportError, "device lost"):
            await link.request(ECUS["5F"], bytes.fromhex("2ED10000"))
        self.assertEqual(fake.write_gatt_char.await_count, 1)
        self.assertTrue(fake.disconnected)

    async def test_no_data_does_not_destroy_a_synchronized_connection(self):
        link, fake, channel = attach_link({"22F190": "22F190\rNO DATA\r>",
                                           "22F191": "7DD 04 62 F1 91 57\r>"})
        with self.assertRaises(NoData):
            await link.request(ECUS["5F"], bytes.fromhex("22F190"))
        result = await link.request(ECUS["5F"], bytes.fromhex("22F191"))
        self.assertEqual(result, bytes.fromhex("62F19157"))
        self.assertFalse(fake.disconnected)
        self.assertIsNone(channel.poisoned)

    async def test_mixed_no_data_closes_and_cannot_be_followed_by_another_request(self):
        for output in MIXED_NO_DATA:
            with self.subTest(output=output):
                link, fake, channel = attach_link({"22F190": "22F190\r" + output})
                with self.assertRaises(ProtocolError):
                    await link.request(ECUS["5F"], bytes.fromhex("22F190"))
                self.assertTrue(fake.disconnected)
                self.assertIsNotNone(channel.poisoned)
                with self.assertRaises(TransportError):
                    await link.request(ECUS["5F"], bytes.fromhex("22F191"))
                self.assertNotIn("22F191", fake.commands)

    async def test_cleanup_phases_have_separate_deadlines_and_preserve_request_error(self):
        for hang_stop, hang_disconnect in ((True, False), (False, True), (True, True)):
            with self.subTest(stop_notify=hang_stop, disconnect=hang_disconnect):
                fake = CleanupBLE({"2ED10000": None}, hang_stop=hang_stop,
                                  hang_disconnect=hang_disconnect)
                link, _, _ = attach_link(fake=fake)
                with patch("audi_workbench.transport.BLE_CLEANUP_TIMEOUT", 0.01, create=True):
                    task = asyncio.create_task(link.request(ECUS["5F"], bytes.fromhex("2ED10000"),
                                                            timeout=0.01))
                    try:
                        await asyncio.wait_for(fake.stop_started.wait(), 0.25)
                        with self.assertRaisesRegex(TransportError, "Timeout waiting for complete ELM"):
                            await asyncio.wait_for(asyncio.shield(task), 0.25)
                    finally:
                        fake.release_cleanup()
                        await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(fake.cleanup_calls, ["stop_notify", "disconnect"])
                self.assertEqual(fake.commands.count("2ED10000"), 1)
                self.assertIsNone(link._client)
                with self.assertRaises(TransportError):
                    await link.request(ECUS["5F"], bytes.fromhex("22D100"))

    async def test_close_keeps_client_reachable_until_both_cleanup_attempts_finish(self):
        fake = CleanupBLE(hang_stop=True, hang_disconnect=True)
        link, _, _ = attach_link(fake=fake)
        task = asyncio.create_task(link.close())
        try:
            await asyncio.wait_for(fake.stop_started.wait(), 0.25)
            self.assertIs(link._client, fake)
            fake.stop_release.set()
            await asyncio.wait_for(fake.disconnect_started.wait(), 0.25)
            self.assertIs(link._client, fake)
        finally:
            fake.release_cleanup()
            await task
        self.assertIsNone(link._client)
        self.assertIsNone(link._notify)

    async def test_cancelled_close_still_attempts_disconnect_and_preserves_cancellation(self):
        fake = CleanupBLE(hang_stop=True)
        link, _, _ = attach_link(fake=fake)
        task = asyncio.create_task(link.close())
        try:
            await asyncio.wait_for(fake.stop_started.wait(), 0.25)
            task.cancel("cancel close")
            with self.assertRaisesRegex(asyncio.CancelledError, "cancel close"):
                await asyncio.wait_for(task, 0.25)
        finally:
            fake.release_cleanup()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(fake.cleanup_calls, ["stop_notify", "disconnect"])
        self.assertTrue(fake.disconnected)
        self.assertIsNone(link._client)

    async def test_cancellation_during_failure_cleanup_does_not_replace_original_error(self):
        fake = CleanupBLE({"22F190": "7DD 04 62 F1 91 57\r>"}, hang_stop=True)
        link, _, _ = attach_link(fake=fake)
        task = asyncio.create_task(link.request(ECUS["5F"], bytes.fromhex("22F190")))
        try:
            await asyncio.wait_for(fake.stop_started.wait(), 0.25)
            task.cancel("cancel cleanup")
            with self.assertRaisesRegex(ProtocolError, "DID does not match"):
                await asyncio.wait_for(task, 0.25)
        finally:
            fake.release_cleanup()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(fake.cleanup_calls, ["stop_notify", "disconnect"])

    async def test_request_cancellation_survives_hanging_cleanup(self):
        fake = CleanupBLE({"2ED10000": None}, hang_stop=True, hang_disconnect=True)
        link, _, _ = attach_link(fake=fake)
        with patch("audi_workbench.transport.BLE_CLEANUP_TIMEOUT", 0.01, create=True):
            task = asyncio.create_task(link.request(ECUS["5F"], bytes.fromhex("2ED10000")))
            try:
                await asyncio.wait_for(fake.write_started.wait(), 0.25)
                task.cancel("cancel original request")
                with self.assertRaisesRegex(asyncio.CancelledError, "cancel original request"):
                    await asyncio.wait_for(task, 0.25)
            finally:
                fake.release_cleanup()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(fake.cleanup_calls, ["stop_notify", "disconnect"])
        self.assertEqual(fake.commands.count("2ED10000"), 1)

    async def test_hanging_ble_cleanup_still_journals_unknown_and_releases_workbench_lock(self):
        fake = CleanupBLE({"2ED10000050000": None}, hang_stop=True, hang_disconnect=True)
        ble, _, _ = attach_link(fake=fake)

        class WriteThroughBLE(DemoLink):
            """Synthetic preflight with the real BLE request/cleanup error path."""
            async def request(self, ecu, payload, *, timeout=8.0):
                if payload[0] == 0x2E:
                    return await ble.request(ecu, payload, timeout=0.01)
                return await super().request(ecu, payload, timeout=timeout)

            async def close(self):
                await ble.close()
                await super().close()

        with TemporaryDirectory() as root:
            store = Store(Path(root))

            async def write_once():
                async with Workbench(store, Dictionary.load(), WriteThroughBLE()) as workbench:
                    snapshot = await workbench.capture()
                    plan_path = workbench.make_plan(snapshot, "demo.display", byte=0, bit=0, value=0)
                    return await workbench.apply(plan_path, store.read(plan_path)["confirmation"],
                                                 enable_writes=True)

            with patch("audi_workbench.transport.BLE_CLEANUP_TIMEOUT", 0.01, create=True):
                task = asyncio.create_task(write_once())
                try:
                    await asyncio.wait_for(fake.stop_started.wait(), 0.5)
                    self.assertEqual(store.read(store.files("transactions")[0])["status"], "attempting")
                    with self.assertRaises(SafetyError), Store(Path(root)).lock():
                        pass
                    receipt_path = await asyncio.wait_for(asyncio.shield(task), 0.25)
                finally:
                    fake.release_cleanup()
                    await asyncio.gather(task, return_exceptions=True)
            receipt = store.read(receipt_path)
            self.assertEqual(receipt["status"], "unknown")
            self.assertIn("Timeout waiting for complete ELM", receipt["error"])
            self.assertEqual(fake.commands.count("2ED10000050000"), 1)
            self.assertEqual(fake.cleanup_calls, ["stop_notify", "disconnect"])
            with Store(Path(root)).lock():
                pass  # Another session can acquire the lock after the unknown receipt is saved.

    async def test_wrong_did_disconnects_and_invalidates_the_session(self):
        link, fake, _ = attach_link({"22F190": "7DD 04 62 F1 91 57\r>"})
        with self.assertRaises(ProtocolError):
            await link.request(ECUS["5F"], bytes.fromhex("22F190"))
        self.assertTrue(fake.disconnected)

    async def test_valid_negative_response_does_not_poison(self):
        link, fake, _ = attach_link({"22F190": "7DD 03 7F 22 31\r>"})
        self.assertEqual(await link.request(ECUS["5F"], bytes.fromhex("22F190")), bytes.fromhex("7F2231"))
        self.assertFalse(fake.disconnected)

    async def test_voltage_is_strictly_parsed(self):
        link, _, _ = attach_link({"ATRV": "12.7V\r>"})
        self.assertEqual(await link.voltage(), 12.7)
        for output in ("unknown\r>", "NaNV\r>", "99V\r>", "12V\r13V\r>"):
            link, _, _ = attach_link({"ATRV": output})
            with self.subTest(output=output), self.assertRaises(ProtocolError):
                await link.voltage()

    async def test_duplicate_names_require_device_address_selection(self):
        devices = [(SimpleNamespace(address=f"uuid{i}", name="IOS-Vlink"),
                    SimpleNamespace(local_name="IOS-Vlink")) for i in range(2)]
        with (
            patch("audi_workbench.transport._bleak", return_value=(object, object)),
            patch("audi_workbench.transport._discover", new=AsyncMock(return_value=devices)),
            self.assertRaisesRegex(TransportError, "exactly one"),
        ):
            await BLELink("IOS-Vlink").open()
