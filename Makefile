.PHONY: run check format test poll start stop restart status logs menubar

# Fallback for callers without a shell PATH, such as the SwiftBar plugin.
UV      := $(or $(shell command -v uv),$(HOME)/.local/bin/uv)
PROJECT := $(CURDIR)
render   = sed -e 's|__UV__|$(UV)|g' -e 's|__PROJECT__|$(PROJECT)|g' -e 's|__HOME__|$(HOME)|g' $(1) > $(2)

run:            ## poll continuously and send alerts (foreground)
	uv run eq-notifier run

check:          ## lint, formatting and type checks
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check

format:         ## fix lint issues and reformat
	uv run ruff check --fix .
	uv run ruff format .

test:           ## run the test suite
	uv run pytest

poll:           ## one poll: source health and recent events, sends nothing
	uv run eq-notifier check

# Background service targets (start, stop, restart, status, logs): launchd on
# macOS, systemd --user elsewhere. Only one of the two files is included.
ifeq ($(shell uname -s),Darwin)
include deploy/macos.mk
else
include deploy/linux.mk
endif
