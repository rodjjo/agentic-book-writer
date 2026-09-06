# Book Writer — convenience targets.
#
# Common usage:
#   make install   # create .venv + pip install -e ".[dev]"
#   make server    # start the bundled fake OpenAI-compatible server
#   make run       # launch the GUI (needs a display; connects to the fake server)
#   make dev       # start the fake server, then launch the GUI on top of it
#   make test      # run the pytest suite

VENV   := .venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
SOCKET := /tmp/book_writer.sock

.PHONY: help install run server dev test clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv and install the package in editable mode (with dev deps)
	python -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

run: ## Run the GUI (auto-connects to unix:$(SOCKET); start a server first with `make server`)
	$(PY) -m app --server unix:$(SOCKET) --autoconnect

server: ## Start the bundled fake OpenAI-compatible server on unix:$(SOCKET)
	$(PY) tools/run_server.py --transport unix --socket $(SOCKET)

dev: ## Start the fake server in the background, launch the GUI, then stop the server
	@$(PY) tools/run_server.py --transport unix --socket $(SOCKET) & \
	server_pid=$$!; \
	trap 'kill $$server_pid 2>/dev/null' EXIT; \
	sleep 1; \
	$(PY) -m app --server unix:$(SOCKET) --autoconnect

test: ## Run the pytest suite (unit + system tests)
	$(PY) -m pytest

clean: ## Remove caches and the scratch socket
	rm -rf .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f $(SOCKET)
