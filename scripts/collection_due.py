"""Dependency-free guard shared by the hourly fallback and manual collector."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def collection_due(document, now, event):
    if event == "push":
        return True  # A new collector version must actually be exercised.
    start = now.replace(minute=50, second=0, microsecond=0)
    if start > now:
        start -= timedelta(hours=1)
    if event == "workflow_dispatch":
        start = now - timedelta(minutes=2)  # Coalesce rapid manual/Cloudflare starts.
    try:
        meta = document["meta"]
        generated = datetime.fromisoformat(meta["generated_at_utc"].replace("Z", "+00:00"))
        fresh = (generated.tzinfo is not None and start <= generated <= now + timedelta(seconds=60)
                 and meta["fresh"] is True and meta["status"] in ("ok", "partial"))
        return not fresh
    except (KeyError, TypeError, ValueError, AttributeError):
        return True


def main():
    try:
        document = json.loads(Path("data/llm_snapshot.json").read_text())
    except (OSError, ValueError):
        document = None
    due = collection_due(document, datetime.now(timezone.utc), os.environ.get("GITHUB_EVENT_NAME", "manual"))
    print("Collect market data" if due else "Snapshot is already current; skip Python setup, dependencies and collection")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as handle:
            handle.write(f"due={str(due).lower()}\n")


if __name__ == "__main__":
    main()
