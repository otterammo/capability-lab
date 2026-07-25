export PYTHONPATH := src

CONFIG_PATH ?= configs/defaults.yaml
export CONFIG_PATH

.PHONY: setup format format-check lint typecheck test test-unit test-contract test-integration test-end-to-end test-docker architecture security check smoke diff-check sandbox-image

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

sandbox-image:
	@python3 -c 'import json, os, re, sys; image = json.load(open(os.environ["CONFIG_PATH"], encoding="utf-8"))["sandbox"]["image"]; valid = isinstance(image, str) and re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?", image); image if valid else sys.exit("invalid sandbox.image"); os.execvp("docker", ["docker", "build", "--tag", image, "--file", "docker/sandbox/Dockerfile", "."])'

test-docker: sandbox-image
	CAPABILITY_LAB_DOCKER_TESTS=1 uv run pytest tests/integration/test_docker_sandbox.py
