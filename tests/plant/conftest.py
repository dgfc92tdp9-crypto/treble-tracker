"""A real NATS server for the transport tests.

**It is never skipped.** A transport test that skips when the broker is
absent is a check that cannot fail, and this project has already shipped two
of those. The whole value of these tests is that they run against a real
broker speaking a real wire protocol — skipping them leaves exactly the
in-process fake that would have passed anyway.

So a missing binary is a hard error naming the fix. `make setup` installs
it, CI installs it, and `tests/plant/test_transport_conformance.py::
test_the_broker_is_real` asserts the server under test is a live process
rather than something the fixture invented.
"""

from __future__ import annotations

import contextlib
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NATS_BINARY = REPO / ".tools" / "nats-server"
KAFKA_HOME = REPO / ".tools" / "kafka"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def nats_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A JetStream-enabled NATS server, for the session."""
    if not NATS_BINARY.exists():
        raise RuntimeError(
            f"{NATS_BINARY} is missing, so the transport tests would be testing nothing. "
            "Run `make setup` (or `make tools`) to install it. This is deliberately an "
            "error rather than a skip: a transport verified only against an in-process "
            "fake has not been verified."
        )
    port = _free_port()
    store = tmp_path_factory.mktemp("jetstream")
    # S603: the argument vector is a repo-relative path this project's own
    # `make tools` wrote, plus a port and a temp dir chosen here. Nothing in
    # it comes from a test, a fixture parameter or the environment.
    process = subprocess.Popen(  # noqa: S603
        [str(NATS_BINARY), "-a", "127.0.0.1", "-p", str(port), "-js", "-sd", str(store)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read().decode() if process.stdout else ""
            raise RuntimeError(f"nats-server exited during startup:\n{output}")
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.25):
            break
        time.sleep(0.05)
    else:
        process.kill()
        raise RuntimeError(f"nats-server did not accept connections on {port} within 20s")

    try:
        yield f"nats://127.0.0.1:{port}"
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)
        process.kill()


@pytest.fixture(scope="session")
def kafka_bootstrap(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A single-node Kafka broker in KRaft mode, for the session.

    Kafka rather than Redpanda because Redpanda ships no broker binary for
    either platform — its releases carry only `rpk` — so running it needs
    Docker. The two speak one wire protocol, so the adapter under test is the
    same; what is not claimed is that Redpanda itself was run here.

    KRaft, so there is no ZooKeeper to start and stop. Startup is ~20s
    against the NATS server's millisecond, which is why this is
    session-scoped and why the broker binaries are cached rather than
    fetched per run.
    """
    start = KAFKA_HOME / "bin" / "kafka-server-start.sh"
    if not start.exists():
        raise RuntimeError(
            f"{KAFKA_HOME} is missing, so the Kafka transport tests would be testing "
            "nothing. Run `make tools`. An error rather than a skip, for the same "
            "reason the NATS fixture raises: a transport verified only against an "
            "in-process fake has not been verified."
        )
    if shutil.which("java") is None:
        raise RuntimeError(
            "java is required to run the Kafka broker. Stated as an error rather than "
            "skipped: an unstated environment assumption is how `make proto` stayed "
            "broken on every clean checkout."
        )

    port, controller = _free_port(), _free_port()
    data = tmp_path_factory.mktemp("kraft")
    config = data / "kraft.properties"
    config.write_text(
        "process.roles=broker,controller\n"
        "node.id=1\n"
        f"controller.quorum.voters=1@127.0.0.1:{controller}\n"
        f"listeners=PLAINTEXT://127.0.0.1:{port},CONTROLLER://127.0.0.1:{controller}\n"
        "inter.broker.listener.name=PLAINTEXT\n"
        f"advertised.listeners=PLAINTEXT://127.0.0.1:{port}\n"
        "controller.listener.names=CONTROLLER\n"
        "listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT\n"
        f"log.dirs={data / 'logs'}\n"
        "offsets.topic.replication.factor=1\n"
        "transaction.state.log.replication.factor=1\n"
        "transaction.state.log.min.isr=1\n"
        "num.partitions=3\n"
    )
    storage = KAFKA_HOME / "bin" / "kafka-storage.sh"
    cluster = subprocess.run(  # noqa: S603
        [str(storage), "random-uuid"], capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(  # noqa: S603
        [str(storage), "format", "-t", cluster, "-c", str(config), "--standalone"],
        capture_output=True,
        check=True,
    )

    log = (data / "server.log").open("wb")
    process = subprocess.Popen([str(start), str(config)], stdout=log, stderr=log)  # noqa: S603
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"kafka exited during startup:\n{(data / 'server.log').read_text()}")
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.25):
            break
        time.sleep(0.25)
    else:
        process.kill()
        raise RuntimeError(f"kafka did not accept connections on {port} within 120s")

    try:
        yield f"127.0.0.1:{port}"
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=30)
        process.kill()
        log.close()
