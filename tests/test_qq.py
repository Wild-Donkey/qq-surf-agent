import json
import hashlib
import hmac
import io
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import agent
from app import qq_events


class QQTests(unittest.TestCase):
    def test_wrong_bot_account_does_not_send(self):
        db = agent.connect(':memory:')
        try:
            agent.collect(db, {}, True)
            db.execute("UPDATE items SET status='approved' WHERE id=1")
            cfg = {'sender': {'mode': 'onebot', 'url': 'http://127.0.0.1:3000', 'group_id': '123', 'expected_self_id': '999'}}
            with patch('app.agent.request', return_value=b'{"status":"ok","retcode":0,"data":{"user_id":888}}') as req:
                with self.assertRaises(ValueError):
                    agent.send(db, cfg)
                self.assertEqual(req.call_count, 1)
                self.assertEqual(db.execute('select status from items where id=1').fetchone()[0], 'approved')
        finally:
            db.close()

    def test_send_image_and_record_success(self):
        db = agent.connect(':memory:')
        try:
            agent.collect(db, {}, True)
            db.execute("UPDATE items SET status='approved',image_url='https://example.com/image.png' WHERE id=1")
            cfg = {'sender': {'mode': 'onebot', 'url': 'http://127.0.0.1:3000', 'group_id': '123', 'send_images': True, 'expected_self_id': '999'}}
            with patch('app.agent.request', side_effect=[b'{"status":"ok","retcode":0,"data":{"user_id":999}}', b'{"status":"ok","retcode":0,"data":{"message_id":42}}']) as req, patch('app.agent.time.sleep'):
                agent.send(db, cfg)
                self.assertEqual(req.call_args[0][1]['group_id'], 123)
                self.assertEqual([p['type'] for p in req.call_args[0][1]['message']], ['text', 'image'])
                self.assertEqual(db.execute('select status from items where id=1').fetchone()[0], 'sent')
        finally:
            db.close()

    def test_event_http_signature(self):
        secret = 'test-secret-with-at-least-24-characters'
        body = json.dumps({'post_type': 'heartbeat'}).encode()
        handler_type = qq_events.make_handler(':memory:', 123, secret)
        def execute(signature):
            handler = object.__new__(handler_type)
            handler.path = '/events'
            handler.headers = {'Content-Length': str(len(body)), 'X-Signature': signature}
            handler.rfile = io.BytesIO(body)
            handler.wfile = io.BytesIO()
            codes = []
            handler.send_error = codes.append
            handler.send_response = codes.append
            handler.send_header = lambda *args: None
            handler.end_headers = lambda: None
            handler.do_POST()
            return codes
        self.assertEqual(execute(''), [403])
        signature = 'sha1=' + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
        self.assertEqual(execute(signature), [200])

    def test_local_onebot_contract(self):
        cfg = {'sender': {'url': 'http://127.0.0.1:3000'}}
        with patch('app.agent.request', return_value=b'{"status":"ok","retcode":0,"data":{"message_id":42}}') as req:
            data = agent.onebot_call(cfg, 'send_group_msg', {'group_id': 123, 'message': [{'type': 'text', 'data': {'text': '[CQ:at,qq=all]'}}]})
            self.assertEqual(data['message_id'], 42)
            self.assertEqual(req.call_args[0][0], 'http://127.0.0.1:3000/send_group_msg')
            self.assertIsInstance(req.call_args[0][1]['message'], list)
        with patch('app.agent.request', return_value=b'{"status":"ok","retcode":0,"data":{}}'):
            with self.assertRaises(ValueError):
                agent.onebot_call(cfg, 'send_group_msg')
        cfg['sender']['url'] = 'https://example.com'
        with self.assertRaises(ValueError):
            agent.onebot_call(cfg, 'get_status')

    def test_only_selected_group_and_no_identity(self):
        with sqlite3.connect(':memory:') as db:
            event = {'post_type': 'message', 'message_type': 'group', 'group_id': 123, 'message_id': 7,
                     'user_id': 888, 'sender': {'nickname': 'private'},
                     'message': [{'type': 'text', 'data': {'text': '梗图讨论'}}, {'type': 'image', 'data': {'url': 'private'}}]}
            self.assertFalse(qq_events.store(db, event, 456))
            self.assertTrue(qq_events.store(db, event, 123))
            self.assertFalse(qq_events.store(db, event, 123))
            self.assertEqual(db.execute('select text from events').fetchone()[0], '梗图讨论')
            columns = [r[1] for r in db.execute('pragma table_info(events)')]
            self.assertEqual(columns, ['id', 'text', 'received'])
            event['message_type'] = 'private'
            self.assertFalse(qq_events.store(db, event, 123))

    def test_text_retention_bound(self):
        with sqlite3.connect(':memory:') as db:
            for i in range(205):
                qq_events.store(db, {'post_type': 'message', 'message_type': 'group', 'group_id': 1,
                                    'message_id': i, 'message': [{'type': 'text', 'data': {'text': 'hello'}}]}, 1)
            self.assertEqual(db.execute('select count(*) from events').fetchone()[0], 200)


if __name__ == '__main__':
    unittest.main()
