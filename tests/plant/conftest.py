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
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NATS_BINARY = REPO / ".tools" / "nats-server"


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
