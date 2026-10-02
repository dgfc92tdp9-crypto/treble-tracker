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
import os
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

#: Where a usable JDK is looked for when `java` on PATH is not one. Homebrew
#: keeps versioned JDKs keg-only, so they are never on PATH by default and
#: never visible to `/usr/libexec/java_home` without a root symlink.
_JAVA_CANDIDATES = (
    Path("/opt/homebrew/opt/openjdk@21"),
    Path("/opt/homebrew/opt/openjdk@17"),
    Path("/opt/homebrew/opt/openjdk"),
    Path("/usr/local/opt/openjdk@21"),
)


def _runs(java: Path) -> bool:
    """Whether ``java`` is an executable this machine can actually start."""
    try:
        return (
            subprocess.run(  # noqa: S603
                [str(java), "-version"], capture_output=True, timeout=60, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _java_home() -> Path:
    """A JDK that runs, not merely one that exists.

    The guard this replaces was ``shutil.which("java") is None``, and it
    **passed on a machine where no Java could run at all**. macOS ships a
    ``/usr/bin/java`` stub whatever is installed, and here it resolved to an
    **x86_64** JDK on an arm64 machine running macOS 27, which no longer has
    Rosetta. Every invocation died with ``Bad CPU type in executable``, and
    the 23 resulting errors surfaced as a bare ``CalledProcessError`` from
    ``kafka-storage.sh`` naming nothing.

    So the guard was failure mode C(ii) — a condition that could not match
    the fault it was written for — and it was *this* guard, whose own comment
    says it exists because an unstated environment assumption kept
    ``make proto`` broken on every clean checkout. Presence was never the
    property worth checking.

    CLAUDE.md §2 says "Apple Silicon native. No Rosetta." That had no
    enforcing mechanism, so the Kafka path depended on translation for as
    long as translation happened to exist. This is the mechanism.
    """
    declared = os.environ.get("JAVA_HOME")
    if declared and _runs(Path(declared) / "bin" / "java"):
        return Path(declared)
    on_path = shutil.which("java")
    if on_path and _runs(Path(on_path)):
        # Trust PATH only once it has proved it runs. `java -version` prints
        # to stderr and exits 0; the stub exits non-zero with no JDK behind it.
        return Path(on_path).resolve().parent.parent
    for home in _JAVA_CANDIDATES:
        if _runs(home / "bin" / "java"):
            return home
    raise RuntimeError(
        "no JDK on this machine can be executed, so the Kafka broker cannot start.\n"
        f"  Tried: JAVA_HOME={declared!r}, `java` on PATH ({on_path!r}), and "
        f"{', '.join(str(c) for c in _JAVA_CANDIDATES)}.\n"
        "  On Apple Silicon this is usually an x86_64 JDK: macOS 27 removed Rosetta, "
        "so Intel JDKs now fail with `Bad CPU type in executable`. Check with "
        "`/usr/libexec/java_home -V` — an entry marked (x86_64) is unusable here.\n"
        "  Fix: `brew install openjdk@21`, which is arm64 and is found by this "
        "fixture without a root symlink.\n"
        "  An error rather than a skip, for the reason stated at the top of this "
        "module: a transport verified only against an in-process fake has not been "
        "verified."
    )


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
    # Resolved rather than merely located, and exported to every child: the
    # Kafka shell scripts prefer $JAVA_HOME/bin/java and fall back to PATH,
    # so a keg-only Homebrew JDK is invisible to them unless named here.
    java_env = {**os.environ, "JAVA_HOME": str(_java_home())}

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
        [str(storage), "random-uuid"],
        capture_output=True,
        text=True,
        check=True,
        env=java_env,
    ).stdout.strip()
    subprocess.run(  # noqa: S603
        [str(storage), "format", "-t", cluster, "-c", str(config), "--standalone"],
        capture_output=True,
        check=True,
        env=java_env,
    )

    log = (data / "server.log").open("wb")
    process = subprocess.Popen(  # noqa: S603
        [str(start), str(config)], stdout=log, stderr=log, env=java_env
    )
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
