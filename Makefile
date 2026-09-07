.PHONY: check lint test

test:
	python -m pytest -q

lint:
	python -m ruff check .

check: lint test


-include Makefile.local
