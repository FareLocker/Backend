.PHONY: help install db-init dev test

UV_CACHE_DIR := $(CURDIR)/.uv-cache
export UV_CACHE_DIR

help:
	@echo "FareLocker backend commands:"
	@echo "  make install  Install and synchronize dependencies"
	@echo "  make db-init  Create missing tables in farelocker.db"
	@echo "  make dev      Initialize the database and start the API"
	@echo "  make test     Run the test suite"

install:
	uv sync

db-init:
	uv run python -c "from app.db import init_db; init_db()"

dev: db-init
	uv run uvicorn app.main:app --reload

test:
	uv run pytest
