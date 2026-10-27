"""Local OneBot event receiver. Never forwards private messages to a model."""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]


def store(db, event, group_id):
    if event.get('post_type') != 'message' or event.get('message_type') != 'group':
        return False
    if str(event.get('group_id')) != str(group_id):
        return False
    message = event.get('message', [])
    if isinstance(message, list):
        text = '\n'.join(part.get('data', {}).get('text', '') for part in message
                         if isinstance(part, dict) and part.get('type') == 'text')
    else:
        # Legacy CQ strings may contain embedded media/contact identifiers; retain only structured text.
        return False
    if not text.strip():
        return False
    message_id = event.get('message_id')
    if message_id is None:
        return False
    db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, text TEXT, received REAL)')
    changed = db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?)',
                        (str(message_id), text[:4000], time.time())).rowcount
    db.execute('DELETE FROM events WHERE received < ?', (time.time() - 7 * 86400,))
    db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY received DESC LIMIT 200)')
    db.commit()
    return bool(changed)


def make_handler(db_path, group_id, secret):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != '/events':
                self.send_error(404)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    self.send_error(413)
                    return
                body = self.rfile.read(size)
                signature = 'sha1=' + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
                if not hmac.compare_digest(self.headers.get('X-Signature', ''), signature):
                    self.send_error(403)
                    return
                event = json.loads(body)
                if not isinstance(event, dict):
                    raise ValueError('Invalid event')
                with sqlite3.connect(str(db_path)) as db:
                    store(db, event, group_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{}')
            except (ValueError, TypeError, AttributeError):
                self.send_error(400)
    return Handler


def main():
    parser = argparse.ArgumentParser(description='只接收选定群的文本事件；仅本机保存')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding='utf-8'))
    group = cfg.get('sender', {}).get('group_id')
    if not str(group or '').isdigit():
        parser.error('先配置 sender.group_id 为实际群号')
    db_path = args.config.resolve().parent / 'private-events.sqlite3'
    if args.show:
        if not db_path.exists():
            print('尚无已接收群消息。')
            return
        with sqlite3.connect(str(db_path)) as db:
            for row in db.execute('SELECT text FROM events ORDER BY received DESC LIMIT 20'):
                print(row[0] + '\n')
        return
    secret = os.environ.get('SURF_EVENT_SECRET', '')
    if len(secret) < 24:
        parser.error('SURF_EVENT_SECRET 至少24个字符，需与桥接端 HTTP 上报 secret 相同')
    server = HTTPServer(('127.0.0.1', args.port), make_handler(db_path, group, secret))
    server.timeout = 1
    print('接收器：http://127.0.0.1:{}/events；按 Ctrl+C 停止。'.format(args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
