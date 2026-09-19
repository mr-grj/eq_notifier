# systemd user unit, included by the Makefile on Linux.
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

restart: start  ## re-render the service file and start it fresh
	systemctl --user restart $(SERVICE)

status:
	systemctl --user status $(SERVICE) --no-pager

logs:
	journalctl --user -u $(SERVICE) -f

menubar:
	@echo "make menubar is macOS-only (SwiftBar)"
