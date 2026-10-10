"""Decide whether a digest is due, so several cron triggers can cover one slot.

GitHub delays and drops scheduled runs, so the digest workflow fires a few times
around each slot. The first run after the slot time sends the digest; the rest exit.

  python3 scripts/slot.py check   → due=true|false to $GITHUB_OUTPUT
  python3 scripts/slot.py done    → remember the current slot as sent

DIGEST_HOURS_UTC (default "7,17"), FORCE=1 makes it due (manual run).
"""
import os
import sys
from datetime import timedelta

from common import load_state, log, now_utc, save_state

LATE_WINDOW_H = 3  # after this, a missed slot is skipped rather than sent late


def current_slot():
    hours = sorted(int(h) for h in (os.environ.get("DIGEST_HOURS_UTC") or "7,17").split(","))
    now = now_utc()
    slots = [now.replace(hour=h, minute=0, second=0, microsecond=0) - timedelta(days=d)
             for d in (0, 1) for h in hours]
    past = [s for s in slots if s <= now]
    slot = max(past)
    return slot, now - slot <= timedelta(hours=LATE_WINDOW_H)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    slot, in_window = current_slot()
    slot_id = slot.strftime("%Y-%m-%dT%H")
    state = load_state("digest_slots.json", {"last": None})
    if cmd == "done":
        save_state("digest_slots.json", {"last": slot_id})
        return
    force = os.environ.get("FORCE") == "1"
    due = force or (in_window and state.get("last") != slot_id)
    log(f"slot {slot_id}: last sent {state.get('last')}, in window {in_window}, force {force} → due {due}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"due={'true' if due else 'false'}\n")


if __name__ == "__main__":
    main()
