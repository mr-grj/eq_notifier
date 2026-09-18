.PHONY: run check format test poll start stop restart status logs

UV      := $(shell command -v uv)
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

ifeq ($(shell uname -s),Darwin)
SERVICE := ro.eq-notifier
PLIST   := $(HOME)/Library/LaunchAgents/$(SERVICE).plist
DOMAIN  := gui/$(shell id -u)

start:          ## install (or refresh) the service and start it at login
	mkdir -p $(dir $(PLIST)) $(HOME)/Library/Logs
	$(call render,deploy/$(SERVICE).plist,$(PLIST))
	-launchctl bootout $(DOMAIN)/$(SERVICE) 2>/dev/null
	launchctl bootstrap $(DOMAIN) $(PLIST)
	@echo "started; logs: make logs"

stop:           ## stop the service and stop starting it at login
	launchctl bootout $(DOMAIN)/$(SERVICE)
	rm -f $(PLIST)

restart:
	launchctl kickstart -k $(DOMAIN)/$(SERVICE)

status:
	launchctl print $(DOMAIN)/$(SERVICE) 2>/dev/null | grep -E 'state|pid|last exit' || echo "not running"

logs:
	tail -n 50 -f $(HOME)/Library/Logs/eq-notifier.log
else
SERVICE := eq-notifier
UNIT    := $(HOME)/.config/systemd/user/$(SERVICE).service

start:          ## install (or refresh) the service and start it at boot
	mkdir -p $(dir $(UNIT))
	$(call render,deploy/$(SERVICE).service,$(UNIT))
	systemctl --user daemon-reload
	systemctl --user enable --now $(SERVICE)
	@echo "started; run 'sudo loginctl enable-linger $$USER' once to survive logout"

stop:           ## stop the service and stop starting it at boot
	systemctl --user disable --now $(SERVICE)
	rm -f $(UNIT)

restart:
	systemctl --user restart $(SERVICE)

status:
	systemctl --user status $(SERVICE) --no-pager

logs:
	journalctl --user -u $(SERVICE) -f
endif
