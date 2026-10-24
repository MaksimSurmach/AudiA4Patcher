import asyncio

import pytest

from audi_workbench.dictionary import Dictionary
from audi_workbench.model import NegativeResponse, NoData, ProtocolError, SafetyError, TransportError
from audi_workbench.service import Workbench
from audi_workbench.simulator import DemoLink
from audi_workbench.store import Store


def test_store_detects_modified_artifact(tmp_path):
    store = Store(tmp_path)
    path = store.save("snapshots", {"schema": "test/v1", "value": "old"})
    path.write_text(path.read_text().replace("old", "new"))
    with pytest.raises(SafetyError, match="integrity"):
        store.read(path)


def test_store_locks_second_process_handle(tmp_path):
    first, second = Store(tmp_path), Store(tmp_path)
    with first.lock(), pytest.raises(SafetyError, match="already"), second.lock():
        pass


def test_masked_edit_preserves_other_bits():
    assert Workbench.edit_value(bytes.fromhex("A510"), byte=0, bit=1, value=1) == bytes.fromhex("A710")
    with pytest.raises(SafetyError):
        Workbench.edit_value(b"\x01", byte=1, bit=0, value=1)


@pytest.fixture
def rig(tmp_path):
    store = Store(tmp_path)
    link = DemoLink(tmp_path / "demo-state.json")
    return Workbench(store, Dictionary.load(), link), link, store


@pytest.fixture
def extended_rig(rig):
    wb, _, _ = rig
    entries = wb.dictionary.entries
    for entry in entries:
        if entry["id"] == "demo.display":
            entry["session"] = 3
    wb.dictionary = Dictionary(entries)
    return rig


def write_payloads(link):
    return [payload for _, payload in link.requests if payload[0] == 0x2E]


async def initial_plan(wb):
    snapshot = await wb.capture()
    plan = wb.make_plan(snapshot, entry_id="demo.display", byte=0, bit=0, value=0)
    return snapshot, plan, Store.read(plan)


@pytest.mark.parametrize("close_fails", [False, True], ids=["close-ok", "close-error"])
async def test_failed_connected_trace_releases_resources_and_session_is_reusable(rig, monkeypatch, close_fails):
    wb, link, store = rig
    connected_error = OSError("connected trace could not be persisted")
    trace = store.trace
    failed = False

    def fail_connected_once(event):
        nonlocal failed
        if event.get("event") == "connected" and not failed:
            failed = True
            raise connected_error
        trace(event)

    monkeypatch.setattr(store, "trace", fail_connected_once)
    if close_fails:
        close = link.close
        failed_close = False

        async def fail_close_once():
            nonlocal failed_close
            await close()
            if not failed_close:
                failed_close = True
                raise TransportError("cleanup failed after closing link")

        monkeypatch.setattr(link, "close", fail_close_once)

    with pytest.raises(OSError) as failure:
        async with wb:
            pytest.fail("Failed entry must not yield an open session")
    assert failure.value is connected_error
    assert not link.is_open
    with Store(store.root).lock():
        pass
    async with wb:
        assert link.is_open
        assert store.read(await wb.capture())["complete"]
    assert not link.is_open


async def test_complete_capture_edit_backup_write_diff_restore(rig):
    wb, link, store = rig
    async with wb:
        original, plan, preview = await initial_plan(wb)
        assert preview["eligible"] and preview["changes"][0]["bits"] == [0]
        assert all(req[0] == 0x22 for _, req in link.requests)
        receipt = await wb.apply(plan, preview["confirmation"], True)
        tx = store.read(receipt)
        assert tx["status"] == "verified" and len(link.writes) == 1
        backup = store.read(tx["backup_path"])
        assert backup["records"]["D100"]["hex"] == "01050000"
        after = await wb.capture()
        changes = wb.diff(original, after)
        assert changes["same_target"] and len(changes["rows"]) == 1
        restore = wb.make_restore(receipt)
        restored = await wb.apply(restore, store.read(restore)["confirmation"], True)
        assert store.read(restored)["status"] == "verified"
        assert link.state["ecus"]["5F"]["records"]["D100"] == "01050000"
        assert len(link.writes) == 2


@pytest.mark.parametrize("mutation,match", [
    (lambda link: setattr(link, "battery_voltage", 11.9), "voltage"),
    (lambda link: setattr(link, "speed_kph", 1), "speed"),
    (lambda link: setattr(link, "rpm", 750), "RPM"),
    (lambda link: link.state["ecus"]["5F"]["records"].update(D100="00050000"), "Stale"),
    (lambda link: link.state["ecus"]["5F"]["records"].update(F189=b"SIM-9999".hex().upper()), "identity"),
    (lambda link: setattr(link, "max_request_bytes", 6), "transport limit"),
])
async def test_write_guard_sends_zero_writes(rig, mutation, match):
    wb, link, _ = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        mutation(link)
        with pytest.raises(SafetyError, match=match):
            await wb.apply(plan, preview["confirmation"], True)
        assert link.writes == []


async def test_confirmation_and_enable_are_independent_guards(rig):
    wb, link, _ = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        with pytest.raises(SafetyError, match="disabled"):
            await wb.apply(plan, preview["confirmation"])
        with pytest.raises(SafetyError, match="Confirmation"):
            await wb.apply(plan, "yes", True)
        assert link.writes == []


async def test_timeout_after_write_is_not_retried_and_can_be_reconciled(rig):
    wb, link, store = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        link.timeout_after_write = True
        receipt = await wb.apply(plan, preview["confirmation"], True)
        assert store.read(receipt)["status"] == "unknown"
        assert len(link.writes) == 1
        with pytest.raises(SafetyError, match="Unresolved"):
            await wb.apply(plan, preview["confirmation"], True)
        with pytest.raises(SafetyError, match="Restore requires"):
            wb.make_restore(receipt)
        link.timeout_after_write = False
        await wb.reconcile(receipt)
        assert store.read(receipt)["status"] == "reconciled_after"
        restore = wb.make_restore(receipt)
        result = await wb.apply(restore, store.read(restore)["confirmation"], True)
        assert store.read(result)["status"] == "verified"
        assert len(link.writes) == 2


async def test_rejected_write_preserves_backup_and_does_not_retry(rig):
    wb, link, store = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        link.deny_writes = True
        receipt = await wb.apply(plan, preview["confirmation"], True)
        tx = store.read(receipt)
        assert tx["status"] == "rejected"
        assert store.read(tx["backup_path"])["records"]["D100"]["hex"] == "01050000"
        assert len([p for _, p in link.requests if p[0] == 0x2E]) == 1


async def test_backup_failure_prevents_any_write(rig, monkeypatch):
    wb, link, store = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        save = store.save
        def fail(kind, document):
            if kind == "snapshots":
                raise OSError("disk full")
            return save(kind, document)
        monkeypatch.setattr(store, "save", fail)
        with pytest.raises(OSError, match="disk full"):
            await wb.apply(plan, preview["confirmation"], True)
        assert link.writes == []


async def test_transaction_creation_failure_prevents_any_write(rig, monkeypatch):
    wb, link, store = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        save = store.save
        failure = OSError("transaction could not be persisted")

        def fail_transaction(kind, document):
            if kind == "transactions":
                raise failure
            return save(kind, document)

        monkeypatch.setattr(store, "save", fail_transaction)
        with pytest.raises(OSError) as caught:
            await wb.apply(plan, preview["confirmation"], True)
        assert caught.value is failure
        assert write_payloads(link) == []
        assert not store.files("transactions")
        backups = [store.read(path) for path in store.files("snapshots")
                   if store.read(path).get("purpose") == "Immediate pre-write configuration backup"]
        assert len(backups) == 1
        assert backups[0]["records"]["D100"]["hex"] == "01050000"
        assert link.state["ecus"]["5F"]["records"]["D100"] == "01050000"


async def test_durable_intent_failure_prevents_any_write(rig, monkeypatch):
    wb, link, store = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        update = store.update
        failure = OSError("attempting intent could not be persisted")

        def fail_attempting(path, document):
            if document.get("status") == "attempting":
                raise failure
            update(path, document)

        monkeypatch.setattr(store, "update", fail_attempting)
        with pytest.raises(OSError) as caught:
            await wb.apply(plan, preview["confirmation"], True)
        assert caught.value is failure
        assert write_payloads(link) == []
        receipt, = store.files("transactions")
        tx = Store.read(receipt)
        assert tx["status"] == "not_sent"
        assert str(failure) in tx["error"]
        assert Store.read(tx["backup_path"])["records"]["D100"]["hex"] == "01050000"
        assert link.state["ecus"]["5F"]["records"]["D100"] == "01050000"


async def test_final_receipt_failure_keeps_intent_and_backup_for_reconnection(rig, monkeypatch):
    wb, link, store = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        update = store.update

        def fail_final_receipt(path, document):
            if document.get("status") == "verified":
                raise OSError("final receipt could not be persisted")
            update(path, document)

        with monkeypatch.context() as patch:
            patch.setattr(store, "update", fail_final_receipt)
            with pytest.raises(SafetyError, match="final receipt could not be saved"):
                await wb.apply(plan, preview["confirmation"], True)
            receipt, = store.files("transactions")
            tx = Store.read(receipt)
            assert tx["status"] == "attempting"
            backup = Store.read(tx["backup_path"])
            assert backup["sha256"] == tx["backup_sha256"]
            assert backup["records"]["D100"]["hex"] == "01050000"
            with pytest.raises(SafetyError, match="Unresolved"):
                await wb.apply(plan, preview["confirmation"], True)
        assert write_payloads(link) == [bytes.fromhex("2ED10000050000")]

    reopened_store = Store(store.root)
    reopened_link = DemoLink(link.state_path)
    async with Workbench(reopened_store, Dictionary.load(), reopened_link) as reopened:
        with pytest.raises(SafetyError, match="Unresolved"):
            await reopened.apply(plan, preview["confirmation"], True)
        await reopened.reconcile(receipt)
        reconciled = Store.read(receipt)
        assert reconciled["status"] == "reconciled_after"
        assert reconciled["actual_hex"] == "00050000"
        assert reconciled["reconciliations"][-1]["previous_status"] == "attempting"
        with pytest.raises(SafetyError, match="Stale"):
            await reopened.apply(plan, preview["confirmation"], True)
        assert Store.read(reopened.make_restore(receipt))["eligible"]
    assert write_payloads(reopened_link) == []
    assert write_payloads(link) == [bytes.fromhex("2ED10000050000")]


@pytest.mark.parametrize("phase", ["write-response", "readback"])
async def test_cancellation_after_write_is_durable_and_reconciled_without_retry(tmp_path, phase):
    class PausingLink(DemoLink):
        def __init__(self):
            super().__init__(tmp_path / "demo-state.json")
            self.reached = asyncio.Event()
            self.release = asyncio.Event()

        async def request(self, ecu, payload, **kwargs):
            response = await super().request(ecu, payload, **kwargs)
            at_write = phase == "write-response" and payload[0] == 0x2E
            at_readback = phase == "readback" and self.writes and payload == bytes.fromhex("22D100")
            if at_write or at_readback:
                self.reached.set()
                await self.release.wait()
            return response

    store = Store(tmp_path)
    link = PausingLink()
    async with Workbench(store, Dictionary.load(), link) as wb:
        _, plan, preview = await initial_plan(wb)
        task = asyncio.create_task(wb.apply(plan, preview["confirmation"], True))
        try:
            await asyncio.wait_for(link.reached.wait(), timeout=5)
            receipt, = store.files("transactions")
            assert Store.read(receipt)["status"] == "attempting"
            task.cancel()
            assert await asyncio.wait_for(task, timeout=5) == receipt
        finally:
            link.release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        tx = Store.read(receipt)
        assert tx["status"] == "unknown"
        assert "CancelledError" in tx["error"]
        backup = Store.read(tx["backup_path"])
        assert backup["sha256"] == tx["backup_sha256"]
        assert backup["records"]["D100"]["hex"] == "01050000"
        with pytest.raises(SafetyError, match="Unresolved"):
            await wb.apply(plan, preview["confirmation"], True)
        assert write_payloads(link) == [bytes.fromhex("2ED10000050000")]

    reopened_link = DemoLink(link.state_path)
    async with Workbench(Store(store.root), Dictionary.load(), reopened_link) as reopened:
        with pytest.raises(SafetyError, match="Unresolved"):
            await reopened.apply(plan, preview["confirmation"], True)
        await reopened.reconcile(receipt)
        reconciled = Store.read(receipt)
        assert reconciled["status"] == "reconciled_after"
        assert reconciled["actual_hex"] == "00050000"
        assert reconciled["reconciliations"][-1]["previous_status"] == "unknown"
        with pytest.raises(SafetyError, match="Stale"):
            await reopened.apply(plan, preview["confirmation"], True)
    assert write_payloads(reopened_link) == []
    assert write_payloads(link) == [bytes.fromhex("2ED10000050000")]


@pytest.mark.parametrize("response_hex", ["5003", "5003003201F4"], ids=["short", "with-timings"])
async def test_extended_session_confirmation_allows_one_verified_write(extended_rig, monkeypatch, response_hex):
    wb, link, _store = extended_rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        request = link.request

        async def session_response(ecu, payload, **kwargs):
            response = await request(ecu, payload, **kwargs)
            return bytes.fromhex(response_hex) if payload == bytes.fromhex("1003") else response

        monkeypatch.setattr(link, "request", session_response)
        receipt = await wb.apply(plan, preview["confirmation"], True)
        assert Store.read(receipt)["status"] == "verified"
        assert [payload for _, payload in link.requests if payload[0] == 0x10] == [bytes.fromhex("1003")]
        assert write_payloads(link) == [bytes.fromhex("2ED10000050000")]
        assert link.state["ecus"]["5F"]["records"]["D100"] == "00050000"


@pytest.mark.parametrize("response_hex,error_type", [
    ("7F1033", NegativeResponse),
    ("5001", ProtocolError),
    ("500300", ProtocolError),
], ids=["rejected", "wrong-session", "malformed-length"])
async def test_invalid_extended_session_response_is_not_sent(extended_rig, monkeypatch, response_hex, error_type):
    wb, link, store = extended_rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        request = link.request

        async def invalid_session_response(ecu, payload, **kwargs):
            response = await request(ecu, payload, **kwargs)
            return bytes.fromhex(response_hex) if payload == bytes.fromhex("1003") else response

        monkeypatch.setattr(link, "request", invalid_session_response)
        with pytest.raises(error_type):
            await wb.apply(plan, preview["confirmation"], True)
        assert write_payloads(link) == []
        receipt, = store.files("transactions")
        tx = Store.read(receipt)
        assert tx["status"] == "not_sent"
        assert Store.read(tx["backup_path"])["records"]["D100"]["hex"] == "01050000"
        assert link.state["ecus"]["5F"]["records"]["D100"] == "01050000"


@pytest.mark.parametrize("mutation,match", [
    (lambda link: link.state["ecus"]["5F"]["records"].update(D100="01060000"), "Value changed"),
    (lambda link: setattr(link, "battery_voltage", 11.8), "voltage"),
    (lambda link: setattr(link, "speed_kph", 1), "speed"),
    (lambda link: setattr(link, "rpm", 800), "RPM"),
], ids=["value-changed", "low-voltage", "moving", "engine-running"])
async def test_session_switch_rechecks_current_value_and_conditions(extended_rig, monkeypatch, mutation, match):
    wb, link, store = extended_rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        request = link.request

        async def change_on_switch(ecu, payload, **kwargs):
            response = await request(ecu, payload, **kwargs)
            if payload == bytes.fromhex("1003"):
                mutation(link)
            return response

        monkeypatch.setattr(link, "request", change_on_switch)
        with pytest.raises(SafetyError, match=match):
            await wb.apply(plan, preview["confirmation"], True)
        assert write_payloads(link) == []
        receipt, = store.files("transactions")
        tx = Store.read(receipt)
        assert tx["status"] == "not_sent"
        assert Store.read(tx["backup_path"])["records"]["D100"]["hex"] == "01050000"


async def test_default_real_entry_stays_blocked_and_raw_preview_available(rig):
    wb, link, store = rig
    async with wb:
        snapshot = await wb.capture()
        plan = wb.make_plan(snapshot, entry_id="vag.coding", byte=12, bit=2, value=1)
        assert not store.read(plan)["eligible"]
        with pytest.raises(SafetyError, match="blocked"):
            await wb.apply(plan, store.read(plan)["confirmation"], True)
        raw = wb.make_plan(snapshot, did=0x22AD, byte=0, value=0x12)
        assert not store.read(raw)["eligible"]
        assert not link.writes


async def test_demo_origin_cannot_authorize_real_link(rig):
    wb, link, _ = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        link.kind = "ble"
        with pytest.raises(SafetyError, match="interchanged"):
            await wb.apply(plan, preview["confirmation"], True)
        assert not link.writes


async def test_unknown_did_does_not_abort_capture_and_scope_is_explicit(rig):
    wb, _, store = rig
    async with wb:
        path = await wb.capture(dids=[0xDEAD], include_dtc=True)
    snapshot = store.read(path)
    assert snapshot["complete"]
    assert not snapshot["records"]["DEAD"]["ok"]
    assert snapshot["dtcs"]["items"] == []
    assert "not a firmware" in snapshot["scope"]


async def test_connection_error_checkpoints_partial_snapshot(rig, monkeypatch):
    wb, link, store = rig
    async with wb:
        real = link.request
        async def fail(ecu, payload, **kw):
            if payload == bytes.fromhex("22F189"):
                raise TransportError("connection lost")
            return await real(ecu, payload, **kw)
        monkeypatch.setattr(link, "request", fail)
        path = await wb.capture()
    snapshot = store.read(path)
    assert not snapshot["complete"]
    assert "connection lost" in snapshot["stopped_reason"]
    assert snapshot["records"]["0600"]["ok"]
    assert "F190" not in snapshot["records"]


async def test_positive_write_with_failed_readback_is_unknown_not_rejected(rig, monkeypatch):
    wb, link, store = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        request = link.request
        async def with_failed_readback(ecu, payload, **kwargs):
            if link.writes and payload == bytes.fromhex("22D100"):
                return bytes.fromhex("7F2231")
            return await request(ecu, payload, **kwargs)
        monkeypatch.setattr(link, "request", with_failed_readback)
        receipt = await wb.apply(plan, preview["confirmation"], True)
        assert store.read(receipt)["status"] == "unknown"
        assert len(link.writes) == 1


async def test_dictionary_edit_after_preview_requires_new_plan(rig):
    wb, link, _ = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        for entry in wb.dictionary.entries:
            if entry["id"] == "demo.display":
                entry["evidence"] += " changed"
        with pytest.raises(SafetyError, match="Dictionary definition changed"):
            await wb.apply(plan, preview["confirmation"], True)
        assert not link.writes


async def test_simultaneous_apply_sends_once_then_stale_rejection(rig):
    wb, link, _store = rig
    async with wb:
        _, plan, preview = await initial_plan(wb)
        results = await asyncio.gather(wb.apply(plan, preview["confirmation"], True),
                                       wb.apply(plan, preview["confirmation"], True), return_exceptions=True)
        assert sum(isinstance(r, SafetyError) for r in results) == 1
        assert len(link.writes) == 1


async def test_demo_long_record_write_and_restore(rig):
    wb, link, store = rig
    async with wb:
        snap = await wb.capture()
        plan = wb.make_plan(snap, entry_id="demo.long_coding", byte=12, bit=2, value=1)
        tx = await wb.apply(plan, store.read(plan)["confirmation"], True)
        assert store.read(tx)["status"] == "verified"
        assert len(link.writes[0][1]) == 23
        restore = wb.make_restore(tx)
        await wb.apply(restore, store.read(restore)["confirmation"], True)
        assert link.state["ecus"]["5F"]["records"]["0600"] == "00" * 20


async def test_synchronized_no_data_keeps_reading_identity(rig, monkeypatch):
    wb, link, store = rig
    async with wb:
        original = link.request
        async def unavailable_coding(ecu, payload, **kwargs):
            if payload == bytes.fromhex("220600"):
                raise NoData("ECU returned NO DATA")
            return await original(ecu, payload, **kwargs)
        monkeypatch.setattr(link, "request", unavailable_coding)
        snapshot = store.read(await wb.capture())
        assert snapshot["complete"]
        assert not snapshot["records"]["0600"]["ok"]
        assert snapshot["identity"]["vin"]


async def test_unresponsive_ecu_is_not_reported_as_successful_capture(rig, monkeypatch):
    wb, link, store = rig
    async with wb:
        async def no_data(*args, **kwargs):
            raise NoData("NO DATA")
        monkeypatch.setattr(link, "request", no_data)
        snapshot = store.read(await wb.capture())
        assert not snapshot["complete"] and not snapshot["identity"]
