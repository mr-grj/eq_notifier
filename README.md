# eq-notifier

A small personal earthquake notifier for Romania. It polls public seismic feeds
(INFP, EMSC, USGS), merges reports of the same earthquake, and pushes one alert
to your phone when an event matches your magnitude and distance settings.

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

## Earthquake sources

| Source | Who | Interface | Notes |
| --- | --- | --- | --- |
| `infp` | INFP, Romania's National Institute for Earth Physics (official) | FDSN event web service, text format | The service is live, but its public catalog has not been updated since September 2025. It is kept enabled because it is the authoritative Romanian source and costs one small request per poll. INFP's real-time alert page now sits behind a CAPTCHA and has no public API, so it is deliberately not scraped. |
| `emsc` | EMSC, European-Mediterranean Seismological Centre | FDSN event web service, GeoJSON | Fast and current for Romania. |
| `usgs` | USGS, United States Geological Survey | FDSN event web service, GeoJSON | Current, mostly for magnitude 4+ events in Europe. |

All three are queried concurrently on every poll, each with its own timeout.
A source that is down, slow, or returns garbage is logged and skipped; the cycle
continues with whatever the other sources returned.

## Alerts and deduplication

An earthquake alerts when **any** source reports it with magnitude at or above
`EQ_MIN_MAGNITUDE` (default 4.0). If you set `EQ_LATITUDE`/`EQ_LONGITUDE`, the
epicentre must also lie within `EQ_RADIUS_KM` (default 300) of you, and the
message includes the distance. Events older than `EQ_MAX_EVENT_AGE_MINUTES`
(default 60) are ignored, so a restart never replays old history.

Sources do not share event IDs, so reports are matched by origin time and
epicentre: two reports within 90 seconds and 100 km of each other are treated
as the same earthquake. Each earthquake produces exactly one alert. The message
uses the values of the highest-priority source that reported it (the order in
`EQ_SOURCES`) and lists every source's magnitude. Alerted events are remembered
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
counts as delivered when at least one succeeds, otherwise it is retried on the
next poll.

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
uv run eq-notifier check               # one poll: source health and recent events, sends nothing
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
make restart    # after editing .env or pulling changes
make stop       # stop and uninstall the service
```

Run it on a machine that does not sleep. A laptop with the lid closed stops
polling. On Linux, run `sudo loginctl enable-linger $USER` once so the service
survives logout.

## Development

```sh
make check    # ruff check, ruff format --check, ty check
make format   # ruff check --fix, ruff format
make test     # pytest
make run      # eq-notifier run
make poll     # eq-notifier check (one poll, sends nothing)
make start / stop / restart / status / logs   # background service, see above
```

Or call the tools directly with `uv run ruff check .`, `uv run ruff format --check .`,
`uv run ty check`, `uv run pytest`.

Layout: `config.py` (settings), `events.py` (normalised earthquake and
deduplication), `sources.py` (feed providers), `notifiers.py` (delivery
channels), `state.py` (remembered alerts), `app.py` (one polling cycle),
`cli.py`. To add a feed or a channel, implement the small protocol in
`sources.py` or `notifiers.py` and register it there.
