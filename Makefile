.PHONY: install test typecheck lint check run-dry brief spend clean

install:
	pip install -e ".[dev,s3]"

test:
	python -m pytest

typecheck:
	python -m mypy

lint:
	python -m ruff check src tests

check: lint typecheck test

run-dry:
	analyst run --dry-run --limit 50 --plain-logs

brief:
	analyst brief

spend:
	analyst spend

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache build dist *.egg-info
