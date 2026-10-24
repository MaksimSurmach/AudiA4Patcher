import asyncio
import json

import pytest

from audi_workbench.dictionary import Dictionary
from audi_workbench.model import ECUS, IDENTITY_DIDS, TransportError
from audi_workbench.simulator import DemoLink


def test_demo_identity_is_exactly_the_dictionary_identity():
    async def run():
        link = DemoLink()
        await link.open()
        identity = Dictionary.load().entry("demo.display")["applies_to"]
        for name, expected in identity.items():
            request = b"\x22" + IDENTITY_DIDS[name].to_bytes(2, "big")
            result = await link.request(ECUS["5F"], request)
            assert result == b"\x62" + request[1:] + bytes.fromhex(expected)
    asyncio.run(run())


def test_demo_write_persists_between_connections(tmp_path):
    async def run():
        state_path = tmp_path / "sim.json"
        first = DemoLink(state_path)
        await first.open()
        assert await first.request(ECUS["5F"], bytes.fromhex("2ED10000050000")) == bytes.fromhex("6ED100")
        await first.close()
        second = DemoLink(state_path)
        await second.open()
        assert await second.request(ECUS["5F"], bytes.fromhex("22D100")) == bytes.fromhex("62D10000050000")
        assert state_path.stat().st_mode & 0o777 == 0o600
    asyncio.run(run())


def test_timeout_after_write_records_ambiguous_realistic_result():
    async def run():
        link = DemoLink()
        await link.open()
        link.timeout_after_write = True
        with pytest.raises(TransportError, match="after"):
            await link.request(ECUS["5F"], bytes.fromhex("2ED10000050000"))
        assert await link.request(ECUS["5F"], bytes.fromhex("22D100")) == bytes.fromhex("62D10000050000")
        assert len(link.writes) == 1
    asyncio.run(run())


def test_wrong_length_and_identity_write_are_rejected():
    async def run():
        link = DemoLink()
        await link.open()
        assert await link.request(ECUS["5F"], bytes.fromhex("2ED10000")) == bytes.fromhex("7F2E13")
        assert await link.request(ECUS["5F"], b"\x2e\xf1\x90" + b"X" * 17) == bytes.fromhex("7F2E31")
        assert not link.writes
    asyncio.run(run())


def test_corrupt_existing_demo_state_is_not_reset(tmp_path):
    path = tmp_path / "sim.json"
    path.write_text('{"schema":"wrong"}')
    async def run():
        with pytest.raises(TransportError, match="state"):
            await DemoLink(path).open()
    asyncio.run(run())
    assert json.loads(path.read_text()) == {"schema": "wrong"}


def test_engine_conditions_and_negative_responses_are_diagnostic_payloads():
    async def run():
        link = DemoLink()
        await link.open()
        link.speed_kph = 42
        link.rpm = 900
        assert await link.request(ECUS["01"], b"\x01\x0d") == b"\x41\x0d\x2a"
        assert await link.request(ECUS["01"], b"\x01\x0c") == b"\x41\x0c\x0e\x10"
        assert await link.request(ECUS["5F"], b"\x22\xaa\xaa") == b"\x7f\x22\x31"
        link.deny_writes = True
        assert await link.request(ECUS["5F"], bytes.fromhex("2ED10000050000")) == bytes.fromhex("7F2E33")
    asyncio.run(run())
