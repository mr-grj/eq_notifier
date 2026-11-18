# eq-notifier

A small personal earthquake notifier for Romania. It listens to EMSC's real-time
stream, polls public seismic feeds (INFP, EMSC, USGS) as a safety net, merges
reports of the same earthquake, and pushes one alert to your phone when an event
matches your magnitude and distance settings.

## What it does and does not do

- It **does** watch official earthquake catalogs and notify you shortly after an
  earthquake has been detected and published.
- It does **not** predict earthquakes, and it is **not** an early-warning system.
  Feeds publish an event seconds to minutes after it happens. Depending on how
  far you are from the epicentre, how fast the agency publishes, and how fast the
  notification reaches your phone, the alert may arrive before, during, or well
  after the shaking at your location. Do not rely on it for safety decisions.
- It is an experimental personal tool, **not a replacement for official emergency
  warning systems** such as RO-ALERT.

## Earthquake feeds

| Feed | Who | Interface | Notes |
| --- | --- | --- | --- |
| `emsc-stream` | EMSC, European-Mediterranean Seismological Centre | Websocket push (`wss://www.seismicportal.eu/standing_order/websocket`) | The fastest path: every new or revised event worldwide arrives a second or two after EMSC publishes it, with no polling delay. On by default; `EQ_EMSC_STREAM=false` turns it off. |
| `infp` | INFP, Romania's National Institute for Earth Physics (official) | FDSN event web service, text format | The service is live, but its public catalog has not been updated since September 2025. It is kept enabled because it is the authoritative Romanian source and costs one small request per poll. INFP's real-time alert page now sits behind a CAPTCHA and has no public API, so it is deliberately not scraped. |
| `emsc` | EMSC, European-Mediterranean Seismological Centre | FDSN event web service, GeoJSON | Fast and current for Romania. |
| `usgs` | USGS, United States Geological Survey | FDSN event web service, GeoJSON | Current, mostly for magnitude 4+ events in Europe. |

The polled sources are queried concurrently every `EQ_POLL_INTERVAL_SECONDS`,
and each answer is acted on the moment it arrives, so a slow source never holds
back a fast one. A source that is down, slow, or returns garbage is logged and
skipped. The stream reconnects on its own with exponential backoff (1 s up to
60 s), detects dead connections with keepalive pings, and reconnects if it has
been silent for 10 minutes. Polling keeps working while the stream is down, and
the stream keeps working while the polled sources are down.

## How it fits together

```
emsc-stream thread ──┐
                     ├──> queue ──> alerter (main thread) ──> notifiers
poller thread ───────┘              decide · deduplicate · remember
```

Each feed runs in its own thread and only hands over reports. All decisions
(freshness, area, magnitude, duplicates, delivery, the state file) happen in
one place, on one thread, so nothing needs locking. If a feed thread ever stops,
the process exits so launchd or systemd restarts it cleanly instead of running
half-blind.

## How fast is an alert?

Alert time = time for EMSC (or another agency) to publish the event + a second or two
for the stream to deliver it + notification delivery. Agencies publish
automatic solutions for Romanian events within minutes, often less. Without the
stream, polling adds up to `EQ_POLL_INTERVAL_SECONDS` on top. Each alert logs
`Earthquake detected Ns after origin`, so you can see the real latency.

This is not an early-warning system. Android's earthquake alerts and RO-ALERT
detect shaking from sensor networks within seconds; for a deep Vrancea event,
strong shaking reaches Bucharest roughly 30 seconds after origin, before any
catalog can publish it.

## Alerts and deduplication

An earthquake alerts when **any** report of it has magnitude at or above
`EQ_MIN_MAGNITUDE` (default 4.0) and lies in your area: within `EQ_RADIUS_KM`
(default 300) of `EQ_LATITUDE`/`EQ_LONGITUDE` if you set them (the message then
includes the distance), otherwise inside the Romania region. A revised magnitude
counts too: an event first reported as M3.8 alerts when EMSC revises it to M4.2. Events older than `EQ_MAX_EVENT_AGE_MINUTES`
(default 60) are ignored, so a restart never replays old history.

Sources do not share event IDs, so reports are matched by origin time and
epicentre: two reports within 90 seconds and 100 km of each other are treated
as the same earthquake. Each earthquake produces exactly one alert, sent on the
first report that qualifies; reports that arrive later only confirm it. When
several sources arrive together, the message uses the highest-priority one (the
order in `EQ_SOURCES`) and lists every source's magnitude. Alerted events are remembered
in a small JSON file (`EQ_STATE_FILE`) so a restart, or a late report from a
source that was down, does not resend the alert.

## Notifications

| Notifier | Cost | Setup |
| --- | --- | --- |
| `ntfy` (default) | Free (ntfy.sh, 250 messages/day) | Install the ntfy app, subscribe to a hard-to-guess topic, set `NTFY_TOPIC`. Alerts are sent with maximum priority. |
| `telegram` | Free | Create a bot with @BotFather, open a chat with it and send it a message, then read `"chat":{"id":...}` from `https://api.telegram.org/bot<TOKEN>/getUpdates` (an empty result means no message was sent yet). Your own user id from @userinfobot works too. |
| `twilio` | Paid | SMS via the Twilio REST API. Trial accounts can only text verified numbers with template bodies, so treat this as a paid option. Kept for those who want SMS. |

No genuinely free, production-usable SMS provider was found, so push
notifications are the default. More than one notifier can be enabled; an alert
counts as delivered when at least one succeeds, otherwise it is retried the next
time a feed reports the event (at the latest on the next poll).

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.14 is installed by uv if missing.

```sh
git clone https://github.com/mr-grj/eq_notifier.git
cd eq_notifier
uv sync
cp .env.example .env   # then edit .env
```

Minimal `.env` for ntfy:

```sh
EQ_NOTIFIERS=ntfy
NTFY_TOPIC=some-hard-to-guess-topic
EQ_LATITUDE=44.43
EQ_LONGITUDE=26.10
```

All settings are environment variables (a `.env` file is loaded if present);
see `.env.example` for the full list. Required values are validated at startup
and every problem is reported at once. Secrets never go in the repository.

## Running

```sh
uv run eq-notifier test-notification   # confirm alerts reach your phone
uv run eq-notifier check               # feed health (incl. the stream) and recent events, sends nothing
uv run eq-notifier run                 # poll continuously and alert
```

`run` stays in the foreground until Ctrl-C. Add `-v` for debug logging.

## Running it all the time

`make start` installs a user service from `deploy/` and starts it: a launchd
agent on macOS (logs in `~/Library/Logs/eq-notifier.log`), a systemd user unit
on Linux (logs in `journalctl --user -u eq-notifier`). It starts at login,
restarts if it crashes, and never resends an alert after a restart.

```sh
make start      # install or refresh the service and start it
make status
make logs
make restart    # after editing .env or pulling changes (same as start on macOS)
make stop       # stop and uninstall the service
```

On macOS, `make menubar` installs [SwiftBar](https://github.com/swiftbar/SwiftBar)
through Homebrew and adds a small menu bar icon (`deploy/eq-notifier.5s.sh`):
green while the service runs, grey when stopped, with the last log lines and
Start / Stop / Restart entries.

A sleeping machine does not poll, so alerts wait until it wakes. On macOS the
service runs under `caffeinate -is`, which keeps the Mac from idle-sleeping
while the service is up. A laptop with its lid closed still sleeps, so keep the
lid open or use a machine that stays on. On Linux, make sure the machine does
not suspend, and run `sudo loginctl enable-linger $USER` once so the service
survives logout. Polls follow the wall clock: after a wake the next poll runs
straight away, and the log warns `No poll for ...s (machine asleep?)` when
polls were missed.

## Development

```sh
make check    # ruff check, ruff format --check, ty check
make format   # ruff check --fix, ruff format
make test     # pytest
make run      # eq-notifier run
make poll     # eq-notifier check (one poll, sends nothing)
make start / stop / restart / status / logs   # background service, see above
make menubar  # macOS status icon via SwiftBar
```

Or call the tools directly with `uv run ruff check .`, `uv run ruff format --check .`,
`uv run ty check`, `uv run pytest`.

Layout: `config.py` (settings and the source/notifier names), `events.py`
(normalised earthquake, deduplication and the `Feed` contract), `sources.py`
(polled FDSN services and the poller), `streams.py` (push feeds),
`notifiers.py` (delivery channels), `state.py` (remembered alerts), `app.py`
(the alerter and the threads that feed it), `cli.py`. To add a polled source,
name it in `SourceName`, subclass `FdsnSource` and register it in `SOURCES`; to
add a channel, name it in `NotifierName` and build it in `build_notifier`; a
push feed implements `Stream` and is added in `build_streams`.
