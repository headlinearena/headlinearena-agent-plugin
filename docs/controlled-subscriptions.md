# Resident news subscriber (development candidate, not published)

`news-stream --max-events 20 --timeout 60` remains bounded. MCP stays bounded.
Only the host-owned CLI below runs continuously, after explicit material
retention approval. It does not call models, submit forecasts or upload bodies.

```sh
python3 scripts/ha.py news-watch --state-dir "$NEWS_PERSISTENT_DIR" --allow-retention
python3 scripts/ha.py news-health --state-dir "$NEWS_PERSISTENT_DIR"
```

The API base stays controlled by the existing CLI configuration. The inbox is
bound to the exact source URL. Never reuse another tenant/host's state directory.
Use a host supervisor such as the event_agents candidate's isolated Compose
stack to start, stop, restart and health-check processes. This command itself
is not installed as a background service. SIGTERM/SIGINT cancel within the
current 30-second network read timeout.

SQLite FULL synchronous + WAL commits the event and persisted cursor together.
A network `received` report precedes durable `persisted`. Processing is separate:
`Inbox.pending()` returns ordered events; a handler calls `Inbox.complete(ids)`
only after durable work succeeds. Its processed cursor advances only across a
contiguous completed prefix. Handlers must be idempotent; a crash after work but
before complete can run that work again. This is at-least-once, never exactly-once.
An inbox-only command does not claim that research has processed the messages.

The default is 1000 pending events, 20 MiB canonical JSON content and 256 KiB per
event. Completed rows may be compacted; pending rows are never dropped. Disk,
WAL and retained run material require host quotas/monitoring. Capacity and old
backlog are health alerts. Queue consumption, alert delivery and material rights
remain the operator's responsibilities.

Reconnects use Last-Event-ID from the persisted cursor, with 1–60 second
exponential backoff and jitter. Versions are deduplicated by cursor; an updated
headline with the same event ID remains a new item. Conflicting duplicates,
unknown older cursors, epoch changes, invalid streams, permission denials and
server gap frames fail closed. Bounded `news-stream` also raises on a gap.

New server journal cursors use `n1:epoch:sequence`. Legacy cursors cannot prove
intermediate-version continuity. Initial windows and pre-journal history have
explicit gaps. `stream_metadata` retains the latest ready frame. Stop the host
and inspect existing pending material before explicitly accepting a gap:

```sh
python3 scripts/ha.py news-recover --state-dir "$NEWS_PERSISTENT_DIR" \
  --cursor "$REVIEWED_SERVER_CHECKPOINT" --reason "$GAP_DECISION" --accept-gap
```

Recovery records the old/new cursors and reason without deleting pending inputs.
It does not manufacture missing history or declare that an Agent saw old inputs.
No automatic reset or fallback exists. The corresponding platform API/journal
must pass staging acceptance before a production host uses this candidate.
