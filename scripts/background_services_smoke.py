"""Exercise the real bundled cobalt under Core's runtime user, without internet.

Run inside the built image. This checks lifecycle, not media-site extraction;
pyLoad remains Off because its authenticated profile belongs to the rig owner.
"""
import asyncio
import logging
import socket

from app.background_services import BackgroundServices
from app.config import Settings
from app.db import EngineSetting, Job, initialize_database, make_engine, make_session_factory


def assert_port(port, *, open):
    with socket.socket() as sock:
        assert (sock.connect_ex(("127.0.0.1", port)) == 0) == open, port


async def smoke():
    settings = Settings.from_env()
    settings.prepare_directories()
    engine = make_engine(settings.database_url)
    initialize_database(engine)
    sessions = make_session_factory(engine)
    # Production uses 180 seconds; shorten only the smoke's idle timer.
    manager = BackgroundServices(sessions, settings, grace=.1)

    def mode(value):
        with sessions() as session:
            row = session.get(EngineSetting, "cobalt")
            if row is None:
                session.add(EngineSetting(engine="cobalt", settings={"mode": value}))
            else:
                row.settings = {"mode": value}
            session.commit()

    try:
        for name in ("pyload", "cobalt"):
            await manager.apply(name)
            assert manager.status(name)["state"] == "stopped"
        for port in (8000, 9000, 9001):
            assert_port(port, open=False)
        print("Fresh Off: no daemon or listener", flush=True)

        mode("always")
        await manager.apply("cobalt")
        child = manager.services["cobalt"].process
        assert manager.status("cobalt")["state"] == "running"
        assert_port(9001, open=True)
        print("Actual Always-on cobalt API and private tunnel ready", flush=True)
        mode("on-demand")
        await manager.apply("cobalt")
        assert child.returncode is not None

        with sessions() as session:
            jobs = [Job(engine="cobalt", url="https://example.org/media", status="starting") for _ in range(2)]
            session.add_all(jobs)
            session.commit()
            ids = [job.id for job in jobs]
        await asyncio.gather(*(manager.acquire("cobalt", job) for job in ids))
        child = manager.services["cobalt"].process
        assert len(manager.services["cobalt"].users) == 2
        await manager.release("cobalt", ids[0])
        await asyncio.sleep(.2)
        assert child.returncode is None
        await manager.release("cobalt", ids[1])
        # Bounded wait for actual process shutdown rather than a fixed sleep.
        async with asyncio.timeout(15):
            while manager.status("cobalt")["state"] != "stopped":
                await asyncio.sleep(.1)
        assert child.returncode is not None
        with sessions() as session:
            assert "cobalt" in session.get(Job, ids[0]).log_text
        for port in (9000, 9001):
            assert_port(port, open=False)
        print("Actual shared On-demand, grace, retained logs and freed ports: PASS", flush=True)
    finally:
        await manager.close()
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(smoke())
