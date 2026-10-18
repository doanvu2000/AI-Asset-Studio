import asyncio
import threading

from studio_core.contracts import Event
from studio_core.store import MemoryEventBus
from studio_core.store.events import STREAM_DROPPED


async def _take(it, n: int) -> list[Event]:
    out: list[Event] = []
    async for ev in it:
        out.append(ev)
        if len(out) == n:
            break
    return out


async def test_seq_monotonic_and_replay() -> None:
    bus = MemoryEventBus()
    for i in range(5):
        bus.publish("step.log", {"i": i})
    assert bus.last_seq == 5
    got = await asyncio.wait_for(_take(bus.subscribe(after_seq=2), 3), 1)
    assert [e.seq for e in got] == [3, 4, 5]


async def test_replay_then_live_without_gaps() -> None:
    bus = MemoryEventBus()
    bus.publish("a")
    it = bus.subscribe(after_seq=0)
    first = await anext(it)
    bus.publish("b")
    bus.publish("c")
    second = await anext(it)
    third = await anext(it)
    assert [first.seq, second.seq, third.seq] == [1, 2, 3]
    await it.aclose()


async def test_ring_buffer_bounded() -> None:
    bus = MemoryEventBus(buffer_size=10)
    for _ in range(25):
        bus.publish("x")
    got = await asyncio.wait_for(_take(bus.subscribe(after_seq=0), 10), 1)
    assert got[0].seq == 16 and got[-1].seq == 25


async def test_slow_subscriber_dropped_with_marker() -> None:
    bus = MemoryEventBus(queue_size=3)
    it = bus.subscribe(after_seq=None)
    task = asyncio.create_task(anext(it))
    await asyncio.sleep(0)
    bus.publish("x")
    assert (await task).seq == 1
    for _ in range(10):
        bus.publish("x")
    seen = [await anext(it) for _ in range(4)]
    assert [e.type for e in seen[:3]] == ["x"] * 3
    assert seen[3].type == STREAM_DROPPED


async def test_publish_from_thread() -> None:
    bus = MemoryEventBus()
    bus.bind_loop(asyncio.get_running_loop())
    it = bus.subscribe(after_seq=None)
    task = asyncio.create_task(anext(it))
    await asyncio.sleep(0)
    threading.Thread(target=bus.publish, args=("step.progress",)).start()
    ev = await asyncio.wait_for(task, 1)
    assert ev.type == "step.progress"
    await it.aclose()
