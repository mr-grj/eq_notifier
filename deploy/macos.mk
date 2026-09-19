# launchd user agent, included by the Makefile on macOS.
SERVICE := ro.eq-notifier
PLIST   := $(HOME)/Library/LaunchAgents/$(SERVICE).plist
DOMAIN  := gui/$(shell id -u)

start:          ## install (or refresh) the service and start it at login
	mkdir -p $(dir $(PLIST)) $(HOME)/Library/Logs
	$(call render,deploy/$(SERVICE).plist,$(PLIST))
	@if launchctl print $(DOMAIN)/$(SERVICE) >/dev/null 2>&1; then \
	    launchctl kickstart -k $(DOMAIN)/$(SERVICE); \
	else \
	    launchctl bootstrap $(DOMAIN) $(PLIST); \
	fi
	@echo "started; logs: make logs"

stop:           ## stop the service and stop starting it at login
	launchctl bootout $(DOMAIN)/$(SERVICE)
	rm -f $(PLIST)

restart: start  ## re-render the service file and start it fresh

status:
	launchctl print $(DOMAIN)/$(SERVICE) 2>/dev/null | grep -E 'state|pid|last exit' || echo "not running"

logs:
	tail -n 50 -f $(HOME)/Library/Logs/eq-notifier.log

PLUGIN_DIR := $(or $(shell defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null),$(HOME)/Library/Application Support/SwiftBar/Plugins)

menubar:        ## install SwiftBar (via Homebrew) and the status-icon plugin
	@[ -d /Applications/SwiftBar.app ] || brew install --cask swiftbar
	mkdir -p "$(PLUGIN_DIR)"
	defaults write com.ameba.SwiftBar PluginDirectory "$(PLUGIN_DIR)"
	$(call render,deploy/eq-notifier.5s.sh,"$(PLUGIN_DIR)/eq-notifier.5s.sh")
	chmod +x "$(PLUGIN_DIR)/eq-notifier.5s.sh"
	open -a SwiftBar
	@echo "plugin installed in $(PLUGIN_DIR); look for the waveform icon in the menu bar"
