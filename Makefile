.PHONY: app audit check clean completion conformance deep desktop desktop-install drift gate golden imports lint mutate mutation proto setup test tools tui types web

# Every `uv run` below refuses to touch the lockfile.
#
# Without this, `uv run` *silently regenerates* uv.lock whenever
# pyproject.toml has drifted from it — measured: exit 0, lock rewritten,
# nothing said. So a dependency edit committed without running `uv lock`
# leaves the committed lock stale, CI relocks in its own ephemeral
# checkout and goes green, and the file that is supposed to make the
# build reproducible quietly stops describing it. Nobody finds out until
# a fresh clone resolves a different dependency set.
#
# `--locked` turns that into exit 2 and "hint: To update the lockfile,
# run `uv lock`", which is a one-command fix and keeps the lock honest.
# Adding a dependency is still a two-step: edit pyproject, run `uv lock`,
# commit both.
export UV_LOCKED := 1

setup: tools          ## create the venv and install everything
	uv venv --python 3.12
	uv pip install -e ".[dev]"
	uv run pre-commit install

# The transport tests run against a real broker, and refuse to skip when it
# is absent (tests/plant/conftest.py). nats-server is a single Apache-2.0
# binary, ~6MB, so fetching it is cheaper than vendoring it or than accepting
# a transport suite that only ever exercises an in-process fake.
tools: .tools/nats-server .tools/kafka  ## fetch the broker binaries the tests need

.tools/nats-server:
	@scripts/fetch-nats.sh

.tools/kafka:
	@scripts/fetch-kafka.sh

lint:
	uv run ruff check .
	uv run ruff format --check .

types:
	uv run mypy treble

test:
	uv run pytest

golden:               ## analytics validation against published references
	uv run pytest -m golden

conformance:          ## renderer conformance suite (I6)
	uv run pytest -m conformance

imports:              ## enforce I7
	uv run lint-imports

audit:                ## dependency vulnerability scan (network; CI + local, not a test)
	uv run pip-audit --skip-editable

deep:                 ## nightly-equivalent property run (2000 examples/property)
	HYPOTHESIS_PROFILE=deep uv run pytest -q

drift:                ## live source schema check — fails when a feed changes shape
	TREBLE_CHECK_DRIFT=1 uv run pytest -q -m drift

mutate:               ## mutation testing: proves the suite detects damage. Slow.
	uv run mutmut run   # scope configured in pyproject [tool.mutmut]

gate:                 ## the single pre-commit gate; fails loudly, never silently
	./scripts/gate.sh

mutation:              ## measure whether the tests would notice wrong numbers
	uv run python scripts/mutation_check.py

completion:            ## print the completion figure (computed, never hand-written)
	uv run python scripts/completion.py --verbose

web:                   ## compile the shared TS renderer (a renderer under conformance)
	./scripts/build_web.sh

proto:                 ## regenerate the gRPC stubs from proto/tapi.proto
	./scripts/gen_proto.sh

check: proto tools lint types imports web test   ## everything CI runs

tui:
	uv run treble tui

desktop:              ## run the desktop client against a dev server
	cd apps/desktop && npm run tauri dev

desktop-install:      ## build the desktop client and install it into ~/Applications
	./scripts/install_desktop.sh

app:                  ## build 'Treble Tracker.app' into ~/Applications
	uv run python scripts/make_app.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
