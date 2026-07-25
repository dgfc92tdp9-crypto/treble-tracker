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

check: lint types imports test   ## everything CI runs

tui:
	uv run treble tui

desktop:
	cd apps/desktop && npm run tauri dev

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
