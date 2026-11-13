.PHONY: help install install-systemd uninstall-systemd dev lint format test clean

SYSTEMD_USER_DIR ?= $(HOME)/.config/systemd/user
CONFIG ?= $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/weather-planner/config.yaml

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install for this user (uv, pipx or pip --user) and the hourly systemd timer
	@if [ "$$(id -u)" = "0" ]; then echo "Refusing to run make install as root; it is a per-user install"; exit 1; fi
	@if command -v uv >/dev/null 2>&1; then \
		uv tool install --force .; \
	elif command -v pipx >/dev/null 2>&1; then \
		pipx install --force .; \
	else \
		echo "Tip: uv or pipx gives an isolated install"; \
		PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --user .; \
	fi
	@$(MAKE) --no-print-directory install-systemd

install-systemd:  ## Install the hourly user timer running "alerts --write"; enable it if configured
	@if [ "$$(id -u)" = "0" ]; then echo "Refusing to run as root; the timer is a user unit"; exit 1; fi
	@if ! command -v systemctl >/dev/null 2>&1; then \
		echo "No systemd: run 'weather-planner alerts --write' from cron or similar instead"; exit 0; \
	fi; \
	bin=$$(command -v weather-planner) || { echo "weather-planner is not on PATH; run make install first"; exit 1; }; \
	install -d "$(SYSTEMD_USER_DIR)" && \
	sed "s|^ExecStart=[^ ]*|ExecStart=$$bin|" systemd/weather-planner.service > "$(SYSTEMD_USER_DIR)/weather-planner.service" && \
	install -m 644 systemd/weather-planner.timer "$(SYSTEMD_USER_DIR)/" && \
	systemctl --user daemon-reload && \
	if [ -f "$(CONFIG)" ]; then \
		systemctl --user enable --now weather-planner.timer; \
	else \
		echo "Timer installed, not enabled: no $(CONFIG) yet."; \
		echo "Write it, then: systemctl --user enable --now weather-planner.timer"; \
	fi

uninstall-systemd:  ## Stop and remove the user timer
	-systemctl --user disable --now weather-planner.timer
	rm -f "$(SYSTEMD_USER_DIR)/weather-planner.service" "$(SYSTEMD_USER_DIR)/weather-planner.timer"
	systemctl --user daemon-reload

dev:  ## Install with dev dependencies (editable)
	@if [ "$$(id -u)" = "0" ]; then echo "Refusing to run make dev as root"; exit 1; fi
	@if ! python -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)'; then \
		echo "Not in a virtualenv.  Create and activate one first:"; \
		echo "  python3 -m venv .venv && . .venv/bin/activate && make dev"; \
		exit 1; \
	fi
	python -m pip install -e ".[dev]"

lint:  ## Run ruff linter and formatter check, and mypy
	python -m ruff check src/ tests/
	python -m ruff format --check src/ tests/
	python -m mypy

format:  ## Auto-format code
	python -m ruff check --fix src/ tests/
	python -m ruff format src/ tests/

test:  ## Run tests
	python -m pytest

clean:  ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info src/*.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
