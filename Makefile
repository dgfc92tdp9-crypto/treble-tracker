.PHONY: setup lint types test golden conformance check tui desktop clean

setup:                ## create the venv and install everything
	uv venv --python 3.12
	uv pip install -e ".[dev]"
	uv run pre-commit install

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

check: lint types imports test   ## everything CI runs

tui:
	uv run treble tui

desktop:
	cd apps/desktop && npm run tauri dev

app:                  ## build 'Treble Tracker.app' into ~/Applications
	uv run python scripts/make_app.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
