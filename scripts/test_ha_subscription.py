"""Real local HTTP, SQLite and process-crash recovery; no platform credentials."""
import io
import json
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).parent))
from ha_client.subscription import Inbox,Gap,Capacity,watch_news,frames


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.epoch=str(uuid.uuid4())

    def tearDown(self):
        self.tmp.cleanup()

    def cursor(self,n):
        return f'n1:{self.epoch}:{n}'

    def test_durable_reception_is_separate_from_contiguous_processing(self):
        box=Inbox(self.root,'fixture')
        for n in range(1,4):
            box.receive(self.cursor(n),{'id':'one','version':n})
        box.complete([3])
        self.assertIsNone(box.get('processed_cursor'))
        box.complete([1])
        self.assertEqual(box.get('processed_cursor'),self.cursor(1))
        box.complete([2])
        self.assertEqual(box.get('processed_cursor'),self.cursor(3))
        box.close()

    def test_duplicates_news_updates_order_and_unknown_epoch(self):
        box=Inbox(self.root,'fixture')
        self.assertTrue(box.receive(self.cursor(1),{'id':'one','title':'first'}))
        self.assertFalse(box.receive(self.cursor(1),{'id':'one','title':'first'}))
        box.receive(self.cursor(3),{'id':'one','title':'updated'})
        with self.assertRaises(Gap):
            box.receive(self.cursor(2),{'id':'two'})
        with self.assertRaises(Gap):
            box.receive(self.cursor(3),{'id':'one','title':'changed under cursor'})
        with self.assertRaises(Gap):
            box.receive(f'n1:{uuid.uuid4()}:4',{'id':'one'})
        self.assertEqual(len(box.pending()),2)
        box.close()

    def test_capacity_never_advances_cursor_and_backlog_is_alerted(self):
        box=Inbox(self.root,'fixture',max_pending=1,max_bytes=1024)
        box.receive(self.cursor(1),{'id':'one'},received_at=1)
        with self.assertRaises(Capacity):
            box.receive(self.cursor(2),{'id':'two'})
        self.assertEqual(box.get('persisted_cursor'),self.cursor(1))
        self.assertIn('backlog_age',box.health()['alerts'])
        box.close()

    def test_compaction_retains_completion_beyond_a_pending_event(self):
        box=Inbox(self.root,'fixture',max_bytes=1024)
        for n in range(1,4):
            box.receive(self.cursor(n),{'id':str(n),'body':'x'*240})
        box.complete([1,3])
        box.receive(self.cursor(4),{'id':'4','body':'x'*240})
        self.assertEqual(box.db.execute('SELECT id FROM inbox ORDER BY id').fetchall(),[(2,),(3,),(4,)])
        box.close()
        box=Inbox(self.root,'fixture',max_bytes=1024)
        box.complete([2])
        self.assertEqual(box.get('processed_cursor'),self.cursor(3))
        self.assertEqual([r['cursor'] for r in box.pending()],[self.cursor(4)])
        box.close()

    def test_process_crash_after_commit_recovers_pending_bytes(self):
        code='''import os,sys
from ha_client.subscription import Inbox
box=Inbox(sys.argv[1],'fixture')
box.receive(sys.argv[2],{'id':'crash-fixture','body':'exact visible text'})
os._exit(7)
'''
        env=dict(__import__('os').environ,PYTHONPATH=str(Path(__file__).parent))
        child=subprocess.run([sys.executable,'-c',code,str(self.root),self.cursor(1)],env=env)
        self.assertEqual(child.returncode,7)
        box=Inbox(self.root,'fixture')
        self.assertEqual(box.get('persisted_cursor'),self.cursor(1))
        self.assertEqual(box.pending()[0]['event']['body'],'exact visible text')
        self.assertIsNone(box.get('processed_cursor'))
        box.close()

    def test_source_or_tenant_change_fails_closed(self):
        box=Inbox(self.root,'fixture-owner-one')
        box.close()
        with self.assertRaises(Gap):
            Inbox(self.root,'fixture-owner-two')

    def test_real_http_disconnect_resume_and_same_id_updated(self):
        requests=[]
        stop=threading.Event()
        epoch=self.epoch
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.0'
            def log_message(self,*_):
                pass
            def do_GET(self):
                requests.append(self.headers.get('Last-Event-ID'))
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                if len(requests)==1:
                    self.wfile.write(f'event: news\nid: n1:{epoch}:1\ndata: {{"id":"same","title":"first"}}\n\n: heartbeat\n\n'.encode())
                else:
                    self.wfile.write(f'event: news\nid: n1:{epoch}:1\ndata: {{"id":"same","title":"first"}}\n\nevent: news\nid: n1:{epoch}:2\ndata: {{"id":"same","title":"update"}}\n\n'.encode())
                self.wfile.flush()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        box=Inbox(self.root,'fixture')
        states=[]
        def report(item):
            states.append(item['state'])
            if item['state']=='persisted' and item['cursor'].endswith(':2'):
                stop.set()
        try:
            self.assertEqual(watch_news(box,f'http://127.0.0.1:{server.server_port}/stream',stop,report),0)
            self.assertEqual(requests[:2],[None,self.cursor(1)])
            self.assertEqual([x['event']['title'] for x in box.pending()],['first','update'])
            self.assertIsNone(box.get('processed_cursor'))
            self.assertEqual(states.count('received'),3)
        finally:
            box.close()
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_explicit_gap_recovery_preserves_pending_and_records_decision(self):
        from ha_client.subscription import recover_cursor
        box=Inbox(self.root,'fixture')
        box.receive(self.cursor(1),{'id':'pending'})
        result=recover_cursor(box,self.cursor(20),'operator-reviewed fixture gap')
        self.assertEqual(result['pending_retained'],1)
        self.assertFalse(result['training_eligible'])
        self.assertEqual(box.get('persisted_cursor'),self.cursor(20))
        self.assertEqual(box.db.execute('SELECT count(*) FROM gap_decisions').fetchone()[0],1)
        self.assertIsNone(box.get('processed_cursor'))
        box.close()

    def test_real_bounded_cli_reports_expired_cursor_without_success(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                self.wfile.write(b'event: gap\ndata: {"code":"cursor_expired"}\n\n')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).parent/'ha.py'),
                'news-stream','--max-events','1','--timeout','3'],
                env=dict(os.environ,HA_BASE_URL=f'http://127.0.0.1:{server.server_port}'),
                capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('cursor_expired',result.stdout+result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_gap_is_fatal_and_oversized_frame_rejected(self):
        stop=threading.Event()
        item=list(frames(io.BytesIO(b'event: gap\ndata: {"code":"expired"}\n\n'),stop))[0]
        self.assertEqual(item['kind'],'gap')
        with self.assertRaises(Capacity):
            list(frames(io.BytesIO(b'data: '+b'x'*262144+b'\n'),stop))


if __name__=='__main__':
    unittest.main()
