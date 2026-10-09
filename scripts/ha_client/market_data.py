"""Small helpers for bounded public news streaming and market evidence."""

import json
import time


def read_sse(response, max_events, deadline=None):
    """Yield news events only; ignore heartbeats/metadata, retain resume IDs."""
    event = None
    cursor = None
    data = []
    count = 0
    for raw in response:
        if deadline is not None and time.monotonic() >= deadline:
            return
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if event == "error" and data:
                raise ValueError("News stream error: " + "\n".join(data))
            if event == "news" and data:
                yield {"cursor": cursor, "event": json.loads("\n".join(data))}
                count += 1
                if count >= max_events:
                    return
            event, cursor, data = None, None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("id:"):
            cursor = line[3:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
