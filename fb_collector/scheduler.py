import json
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db
from .runner import run_project


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def next_run(schedule_type, config, base=None):
    base = base or datetime.now(timezone.utc)
    if schedule_type == "once":
        return None
    if schedule_type == "minutes":
        return base + timedelta(minutes=max(1, int(config.get("interval", 30))))
    if schedule_type == "hours":
        return base + timedelta(hours=max(1, int(config.get("interval", 1))))
    if schedule_type == "daily":
        hour = int(config.get("hour", 9))
        minute = int(config.get("minute", 0))
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate
    if schedule_type == "weekly":
        weekday = int(config.get("weekday", 0))
        hour = int(config.get("hour", 9))
        minute = int(config.get("minute", 0))
        days = (weekday - base.weekday()) % 7
        candidate = (base + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            candidate += timedelta(days=7)
        return candidate
    if schedule_type == "monthly":
        day = max(1, min(28, int(config.get("day", 1))))
        hour = int(config.get("hour", 9))
        minute = int(config.get("minute", 0))
        candidate = base.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            year = candidate.year + (1 if candidate.month == 12 else 0)
            month = 1 if candidate.month == 12 else candidate.month + 1
            candidate = candidate.replace(year=year, month=month)
        return candidate
    return None


class SchedulerThread(threading.Thread):
    def __init__(self, interval=30):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_event = threading.Event()
        self.worker_threads = []

    def run(self):
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(self.interval)

    def tick(self):
        now = datetime.now(timezone.utc)
        for task in db.list_tasks():
            if not task["enabled"] or task["schedule_type"] == "manual":
                continue
            due = parse_dt(task["next_run_at"])
            if due and due <= now:
                thread = threading.Thread(
                    target=run_project,
                    args=(task["project_id"], task["id"], task["run_policy_override"]),
                    daemon=True,
                )
                thread.start()
                self.worker_threads.append(thread)
                config = json.loads(task["schedule_config_json"] or "{}")
                upcoming = next_run(task["schedule_type"], config, now)
                data = {
                    "project_id": task["project_id"],
                    "name": task["name"],
                    "enabled": bool(task["enabled"]) and upcoming is not None,
                    "schedule_type": task["schedule_type"],
                    "schedule_config": config,
                    "next_run_at": upcoming.isoformat() if upcoming else None,
                    "run_policy_override": task["run_policy_override"],
                }
                db.update_task(task["id"], data)

    def stop(self):
        self.stop_event.set()
