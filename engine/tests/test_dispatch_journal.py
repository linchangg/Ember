from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import asyncio
import pytest
from emberline.notify.dispatch import DispatchJournal
from emberline.notify import cartesia
from emberline.sync.poller import Poller


def test_reservation_survives_restart_and_never_stores_recipient(tmp_path):
    path = tmp_path / "dispatch.db"
    assert DispatchJournal(path).reserve("alert1")[0]
    fresh, row = DispatchJournal(path).reserve("alert1")
    assert not fresh and row.state == "reserved"
    DispatchJournal(path).finish("alert1", "placed", "provider1", "placed")
    assert DispatchJournal(path).reserve("alert1")[1].call_id == "provider1"
    with pytest.raises(ValueError):
        DispatchJournal(path).finish("alert1", "placed", "x", "placed")


def test_two_workers_only_one_reserves(tmp_path):
    journal = DispatchJournal(tmp_path / "dispatch.db")
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(lambda _: journal.reserve("same")[0], range(2))) == 1


def test_duplicate_approval_calls_provider_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cartesia, "configured", lambda: True)

    async def call(*_):
        calls.append(1)
        return cartesia.CallResult(True, "provider1", "placed")

    monkeypatch.setattr(cartesia, "place_call", call)

    class Sink:
        async def update_alert_call(self, *_):
            pass

        async def append_events(self, *_):
            pass

    loop = SimpleNamespace(
        root=tmp_path, ctx=SimpleNamespace(scenario_id="synthetic"), branch="main"
    )
    local = SimpleNamespace(duty_officer_number="+15555550100")
    p = Poller(Sink(), SimpleNamespace(sim_now=lambda: 0), loop, local_state=local)
    alert = SimpleNamespace(alert_id="test-alert")

    async def run():
        assert await p._call(alert) == "placed"
        assert await p._call(alert) == "placed"

    asyncio.run(run())
    assert len(calls) == 1


def test_uncertain_attempt_is_not_retried(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cartesia, "configured", lambda: True)

    async def call(*_):
        calls.append(1)
        raise TimeoutError()

    monkeypatch.setattr(cartesia, "place_call", call)

    class Sink:
        async def update_alert_call(self, *_):
            pass

        async def append_events(self, *_):
            pass

    loop = SimpleNamespace(
        root=tmp_path, ctx=SimpleNamespace(scenario_id="synthetic"), branch="main"
    )
    p = Poller(
        Sink(),
        SimpleNamespace(sim_now=lambda: 0),
        loop,
        local_state=SimpleNamespace(duty_officer_number="+15555550100"),
    )

    async def run():
        assert await p._call(SimpleNamespace(alert_id="a")) == "delivery_unknown"
        assert await p._call(SimpleNamespace(alert_id="a")) == "delivery_unknown"

    asyncio.run(run())
    assert len(calls) == 1
