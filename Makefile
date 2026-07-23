export PYTHONPATH := src

.PHONY: setup format format-check lint typecheck test test-unit test-contract test-integration test-end-to-end architecture security check smoke diff-check

setup:
	uv sync --all-groups
	mkdir -p .lab
	uv run alembic upgrade head
	uv run python scripts/prepare_fixture.py
	uv run lab doctor

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

typecheck:
	uv run pyright

test: test-unit test-contract test-integration test-end-to-end

test-unit:
	uv run pytest tests/unit

test-contract:
	uv run pytest tests/contract

test-integration:
	uv run pytest tests/integration

test-end-to-end:
	uv run pytest tests/end_to_end

architecture:
	uv run lint-imports

security:
	uv run pip-audit

check: format-check lint typecheck test architecture security

smoke:
	uv run lab smoke

diff-check:
	git diff --check
	git diff --cached --check
	git status --short
