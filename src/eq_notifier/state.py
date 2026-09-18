import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eq_notifier.events import Earthquake

log = logging.getLogger(__name__)


class SeenEvents:
    """Remembers reports that have already been alerted on, in a small JSON file.

    Matching uses the same rule as cross-source deduplication, so after a restart a
    report from a source that was down earlier still counts as already alerted.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._events: list[Earthquake] = self._load()

    def _load(self) -> list[Earthquake]:
        if not self.path.exists():
            return []
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
            return [
                Earthquake(
                    source=record["source"],
                    event_id=record["event_id"],
                    time=datetime.fromisoformat(record["time"]),
                    magnitude=float(record["magnitude"]),
                    latitude=float(record["latitude"]),
                    longitude=float(record["longitude"]),
                    depth_km=record.get("depth_km"),
                    region=record.get("region", ""),
                    url=record.get("url"),
                )
                for record in records
            ]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            log.warning("Ignoring unreadable state file %s: %s", self.path, exc)
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = [{**asdict(event), "time": event.time.isoformat()} for event in self._events]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def __contains__(self, event: Earthquake) -> bool:
        return any(seen.is_same_event(event) for seen in self._events)

    def add(self, *events: Earthquake) -> None:
        for event in events:
            if event not in self:
                self._events.append(event)
        self._save()

    def prune(self, max_age: timedelta, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(UTC)) - max_age
        kept = [event for event in self._events if event.time >= cutoff]
        if len(kept) != len(self._events):
            self._events = kept
            self._save()
