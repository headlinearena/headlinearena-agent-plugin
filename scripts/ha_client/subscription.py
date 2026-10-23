"""Opt-in host subscriber. SQLite durable inbox; at-least-once processing.

No model, prediction, upload, credential creation or training calls. Network
resume tracks durable reception, independently from a handler's completion.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Gap(ValueError):
    pass


class Capacity(RuntimeError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def position(cursor):
    try:
        if cursor.startswith('n1:'):
            _, epoch, number = cursor.split(':')
        else:
            epoch, number = cursor.split(':')
        if len(cursor)>80 or str(uuid.UUID(epoch))!=epoch or not number.isascii() or not number.isdigit():
            raise ValueError
        return epoch, int(number)
    except (AttributeError, ValueError):
        raise Gap('unsupported_cursor_protocol') from None


class Inbox:
    def __init__(self, directory, source, *, max_pending=1000, max_bytes=20*1024*1024):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or not source or not 1 <= max_pending <= 100000 or max_bytes < 1024:
            raise ValueError('invalid_inbox_configuration')
        self.path = self.directory/'subscription.sqlite3'
        if self.path.is_symlink():
            raise ValueError('unsafe_inbox')
        self.db = sqlite3.connect(str(self.path), timeout=5)
        os.chmod(self.path, 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox(
                id INTEGER PRIMARY KEY, cursor TEXT UNIQUE NOT NULL, event_id TEXT,
                body BLOB NOT NULL, sha256 TEXT NOT NULL, received_at REAL NOT NULL,
                persisted_at REAL NOT NULL, processed_at REAL,
                kind TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS runs(
                run_ref TEXT PRIMARY KEY, started_at REAL NOT NULL, status TEXT NOT NULL,
                triggers BLOB NOT NULL, result BLOB);
        ''')
        old = self.get('source')
        if old and old != source:
            self.db.close()
            raise Gap('source_or_tenant_changed_use_separate_inbox')
        with self.db:
            self.set('source', source)
        self.max_pending, self.max_bytes = max_pending, max_bytes

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        self.db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (key,str(value)))

    def heartbeat(self, state='connected'):
        with self.db:
            self.set('heartbeat_at', time.time())
            self.set('state',state)

    def receive(self, cursor, event, kind='news', received_at=None):
        body = encoded(event)
        if len(body) > 262144:
            raise Capacity('event_size_limit')
        pos = position(cursor)
        digest = hashlib.sha256(body).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.db.execute('SELECT sha256 FROM inbox WHERE cursor=?', (cursor,)).fetchone()
            if old:
                if old[0] != digest:
                    raise Gap('cursor_content_changed')
                self.db.rollback()
                return False
            last = self.get('persisted_cursor')
            if last:
                prior = position(last)
                if prior[0] != pos[0]:
                    raise Gap('cursor_epoch_changed')
                if pos[1] <= prior[1]:
                    raise Gap('out_of_order_or_unknown_duplicate')
            pending, total = self.db.execute('SELECT count(CASE WHEN processed_at IS NULL THEN 1 END),coalesce(sum(length(body)),0) FROM inbox').fetchone()
            # Completed rows may be compacted, pending rows are never evicted.
            if total+len(body) > self.max_bytes:
                self.db.execute('DELETE FROM inbox WHERE processed_at IS NOT NULL')
                total = self.db.execute('SELECT coalesce(sum(length(body)),0) FROM inbox').fetchone()[0]
            if pending >= self.max_pending or total+len(body) > self.max_bytes:
                raise Capacity('durable_queue_capacity')
            now = time.time()
            self.db.execute('INSERT INTO inbox(cursor,event_id,body,sha256,received_at,persisted_at,kind) VALUES (?,?,?,?,?,?,?)',
                            (cursor,str(event.get('id','')),body,digest,received_at or now,now,kind))
            self.set('persisted_cursor',cursor)
            self.set('persisted_at',now)
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    def pending(self, limit=100):
        rows = self.db.execute('SELECT id,cursor,body,received_at,persisted_at,kind FROM inbox WHERE processed_at IS NULL ORDER BY id LIMIT ?',
                               (min(limit,1000),)).fetchall()
        return [dict(id=r[0],cursor=r[1],event=json.loads(r[2]),received_at=r[3],persisted_at=r[4],kind=r[5]) for r in rows]

    def complete(self, ids):
        with self.db:
            for event_id in ids:
                self.db.execute('UPDATE inbox SET processed_at=? WHERE id=? AND processed_at IS NULL',(time.time(),event_id))
            # Only the contiguous processed prefix is a processing watermark.
            first = self.db.execute('SELECT min(id) FROM inbox WHERE processed_at IS NULL').fetchone()[0]
            row = self.db.execute('SELECT cursor FROM inbox WHERE processed_at IS NOT NULL AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT 1', (first,first)).fetchone()
            if row:
                self.set('processed_cursor',row[0])

    def health(self, max_age=120):
        pending, oldest = self.db.execute('SELECT count(*),min(received_at) FROM inbox WHERE processed_at IS NULL').fetchone()
        heartbeat = float(self.get('heartbeat_at') or 0)
        problems = []
        if time.time()-heartbeat > max_age:
            problems.append('subscriber_stale')
        if oldest and time.time()-oldest > max_age:
            problems.append('backlog_age')
        if self.db.execute("SELECT count(*) FROM runs WHERE status='unknown'").fetchone()[0]:
            problems.append('run_unknown')
        state = self.get('state')
        if state in ('gap','capacity','denied','run_unknown'):
            problems.append(state)
        return dict(state=state,pending=pending,oldest_received_at=oldest,
                    persisted_cursor=self.get('persisted_cursor'),processed_cursor=self.get('processed_cursor'),
                    alerts=problems,healthy=not problems)


def frames(response, stop):
    event, cursor, data, size = None,None,[],0
    while not stop.is_set():
        raw = response.readline(262145)
        if not raw:
            return
        if len(raw) > 262144:
            raise Capacity('sse_line_limit')
        line = raw.decode('utf-8').rstrip('\r\n')
        if line.startswith(':'):
            yield {'kind':'heartbeat'}
        elif not line:
            if data:
                yield {'kind':event or 'message','cursor':cursor,'event':json.loads('\n'.join(data))}
            event,cursor,data,size = None,None,[],0
        elif line.startswith('event:'):
            event = line[6:].strip()
        elif line.startswith('id:'):
            cursor = line[3:].strip()
        elif line.startswith('data:'):
            size += len(raw)
            if size > 262144:
                raise Capacity('sse_frame_limit')
            data.append(line[5:].lstrip())


def watch_news(inbox, url, stop=None, report=None):
    stop = stop or threading.Event()
    report = report or (lambda value: print(json.dumps(value),flush=True))
    delay = 1.0
    while not stop.is_set():
        started = time.monotonic()
        headers = {'Accept':'text/event-stream'}
        if inbox.get('persisted_cursor'):
            headers['Last-Event-ID'] = inbox.get('persisted_cursor')
        try:
            request = urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(request,timeout=30) as response:
                if 'text/event-stream' not in response.headers.get('Content-Type',''):
                    raise Gap('expected_sse')
                for item in frames(response,stop):
                    inbox.heartbeat()
                    if item['kind'] in ('news','checkpoint'):
                        report({'state':'received','cursor':item['cursor']})
                        inbox.receive(item['cursor'],item['event'],item['kind'],time.time())
                        report({'state':'persisted','cursor':item['cursor']})
                    elif item['kind'] == 'ready':
                        with inbox.db:
                            inbox.set('stream_metadata',encoded(item['event']).decode())
                    elif item['kind'] == 'gap':
                        raise Gap(item['event'].get('code','server_gap'))
                    elif item['kind'] == 'error':
                        raise OSError('server_temporarily_unavailable')
        except Gap:
            inbox.heartbeat('gap')
            report({'state':'gap','action_required':True})
            return 2
        except Capacity:
            inbox.heartbeat('capacity')
            report({'state':'capacity','action_required':True})
            return 2
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403,410,422):
                inbox.heartbeat('denied' if exc.code in (401,403) else 'gap')
                report({'state':inbox.get('state'),'action_required':True})
                return 2
            inbox.heartbeat('reconnecting')
        except (OSError,ValueError):
            inbox.heartbeat('reconnecting')
        if time.monotonic()-started >= 30:
            delay = 1.0
        stop.wait(min(60,random.uniform(delay/2,delay*1.5)))
        delay = min(60,delay*2)
    inbox.heartbeat('stopped')
    return 0


def run_watch(directory, url, *, max_pending=1000,max_bytes=20*1024*1024):
    # One subscriber per inbox. Do not hold this lock in the processing worker.
    import fcntl
    root = Path(directory)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock_path = root/'.subscriber.lock'
    if lock_path.is_symlink():
        raise ValueError('unsafe_subscriber_lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        inbox = Inbox(root,url,max_pending=max_pending,max_bytes=max_bytes)
        stop = threading.Event()
        previous = {s:signal.signal(s,lambda *_:stop.set()) for s in (signal.SIGTERM,signal.SIGINT)}
        try:
            return watch_news(inbox,url,stop)
        finally:
            for s,handler in previous.items():
                signal.signal(s,handler)
            inbox.close()


def recover_cursor(inbox, new_cursor, reason):
    """Explicit operator acceptance of a gap. Never removes pending inputs."""
    position(new_cursor)
    if not isinstance(reason,str) or not reason.strip() or len(reason)>500:
        raise ValueError('gap_recovery_reason_required')
    with inbox.db:
        inbox.db.execute('CREATE TABLE IF NOT EXISTS gap_decisions(id INTEGER PRIMARY KEY,recorded_at REAL NOT NULL,old_cursor TEXT,new_cursor TEXT NOT NULL,reason TEXT NOT NULL)')
        count=inbox.db.execute('SELECT count(*) FROM gap_decisions').fetchone()[0]
        if count>=1000:
            raise Capacity('gap_decision_capacity')
        inbox.db.execute('INSERT INTO gap_decisions(recorded_at,old_cursor,new_cursor,reason) VALUES (?,?,?,?)',
                         (time.time(),inbox.get('persisted_cursor'),new_cursor,reason))
        inbox.set('persisted_cursor',new_cursor)
        inbox.set('state','stopped')
    return {'state':'gap_accepted','cursor':new_cursor,'pending_retained':inbox.db.execute('SELECT count(*) FROM inbox WHERE processed_at IS NULL').fetchone()[0],
            'training_eligible':False}
