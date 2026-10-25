"""Events waiting to be sent: errors from failed wakes, and the outcome of updates. Kept in
outbox.json on flash (written only when something goes wrong, so rarely) and sent with
POST /device/v1/events at the next wake that reaches the API. At most MAX, the oldest dropped."""

import json

OUTBOX = "outbox.json"
MAX = 8


def _load():
    try:
        with open(OUTBOX) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(events):
    if events:
        with open(OUTBOX, "w") as f:
            json.dump(events[-MAX:], f)
    else:
        try:
            import os
            os.remove(OUTBOX)
        except OSError:
            pass


def add(type_, msg, data=None):
    events = _load()
    events.append({"type": type_, "msg": msg[:200], "data": data})
    _save(events)


def take():
    """All waiting events, removed from the outbox. Put them back() if sending fails."""
    events = _load()
    if events:
        _save([])
    return events


def put_back(events):
    _save(events + _load())
