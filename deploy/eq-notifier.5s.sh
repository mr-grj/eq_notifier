#!/bin/bash
# SwiftBar menu bar plugin: shows whether the launchd service is running.
# `make menubar` fills in the project path and installs it.
# <xbar.title>eq-notifier</xbar.title>
# <xbar.desc>Status and controls for the earthquake notifier service</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

PROJECT="__PROJECT__"
LOG="$HOME/Library/Logs/eq-notifier.log"
MAKE="bash=/usr/bin/make param1=-C param2=$PROJECT"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

pid=$(launchctl print "gui/$(id -u)/ro.eq-notifier" 2>/dev/null | awk '/^\tpid = /{print $3}')

if [ -n "$pid" ]; then
    echo "| sfimage=waveform.path.ecg sfcolor=#34c759"
    echo "---"
    echo "eq-notifier is running (pid $pid)"
else
    echo "| sfimage=waveform.path.ecg sfcolor=#8e8e93"
    echo "---"
    echo "eq-notifier is stopped | color=red"
fi

echo "---"
if [ -s "$LOG" ]; then
    tail -n 5 "$LOG" | while IFS= read -r line; do
        echo "$line | font=Menlo size=11 length=110"
    done
else
    echo "no log yet"
fi

echo "---"
if [ -n "$pid" ]; then
    echo "Restart | $MAKE param3=restart terminal=false refresh=true"
    echo "Stop | $MAKE param3=stop terminal=false refresh=true"
else
    echo "Start | $MAKE param3=start terminal=false refresh=true"
fi
echo "Open log | bash=/usr/bin/open param1=$LOG terminal=false"
echo "Poll once in Terminal | $MAKE param3=poll terminal=true"
