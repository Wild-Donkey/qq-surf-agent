#!/usr/bin/env python3
"""Persistent group-to-group relay. No third-party dependencies or external LLM calls."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import hmac
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import agent
import social
import alerts
from memory import MarkdownMemory
from sessions import SessionPool

ROOT = Path(__file__).resolve().parent
MARK = '🪨 群间搬运｜'


def connect(path):
    db = sqlite3.connect(str(path), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS events(group_id TEXT, message_id TEXT, fingerprint TEXT,
      text TEXT, images TEXT, created REAL, PRIMARY KEY(group_id,message_id));
    CREATE TABLE IF NOT EXISTS seen(group_id TEXT, fingerprint TEXT, created REAL,
      PRIMARY KEY(group_id,fingerprint));
    CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE,
      source_group TEXT, text TEXT, images TEXT, created REAL, status TEXT);
    CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY, candidate_id INTEGER,
      target_group TEXT, fingerprint TEXT, status TEXT, created REAL, updated REAL,
      message_id TEXT, error TEXT, UNIQUE(candidate_id,target_group));
    CREATE TABLE IF NOT EXISTS memory(group_id TEXT PRIMARY KEY, summary TEXT, updated REAL);
    CREATE TABLE IF NOT EXISTS metrics(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS group_activity(group_id TEXT PRIMARY KEY,last_member_message REAL);
    CREATE TABLE IF NOT EXISTS operator_pause(group_id TEXT PRIMARY KEY,until_time REAL);
    CREATE TABLE IF NOT EXISTS operator_log(id INTEGER PRIMARY KEY,target TEXT,kind TEXT,
      fingerprint TEXT,status TEXT,message_id TEXT,created REAL);
    ''')
    if 'kind' not in {r[1] for r in db.execute('PRAGMA table_info(candidates)')}:
        db.execute("ALTER TABLE candidates ADD COLUMN kind TEXT DEFAULT 'share'")
    social.ensure_schema(db)
    alerts.ensure_schema(db)
    db.commit()
    return db


def configured_groups(cfg):
    return {str(x['id']): x for x in cfg['groups'] if x.get('enabled', True)}


def group_is_active(db,group,cfg,now=None):
    now=time.time() if now is None else now
    row=db.execute('SELECT last_member_message FROM group_activity WHERE group_id=?',(str(group),)).fetchone()
    return bool(row and 0<=now-row[0]<=cfg.get('inactive_group_days',14)*86400)


def normalize(event):
    parts = event.get('message')
    if not isinstance(parts, list):
        return '', []
    text = []
    images = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        data = part.get('data', {})
        if not isinstance(data, dict):
            continue
        if part.get('type') == 'text':
            text.append(str(data.get('text', '')))
        elif part.get('type') == 'image':
            url = str(data.get('url', ''))
            if urlsplit(url).scheme == 'https':
                # Never accept arbitrary local files or CQ instructions from source messages.
                images.append({'url': url, 'key': str(data.get('file') or urlsplit(url).path)})
    return '\n'.join(text).strip()[:6000], images[:3]


def fingerprint(text, images):
    normalized = re.sub(r'\s+', '', text).casefold()
    # Image file identifiers survive short-lived CDN URL changes.
    keys = sorted(x['key'] for x in images)
    return hashlib.sha256(json.dumps(['images',keys] if keys else ['text',normalized], ensure_ascii=False).encode()).hexdigest()


def eligible(text, images, cfg):
    if MARK in text or not text and not images:
        return False
    if any(word.casefold() in text.casefold() for word in cfg.get('exclude_keywords', [])):
        return False
    # Standalone chat fragments, replies and mentions do not make useful relay material.
    if re.search(r'(1[3-9]\d{9}|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|身份证|银行卡|验证码)', text):
        return False
    return bool(images or re.search(r'https?://[^\s]+', text)) and len(text) <= 1200


def refresh_memory(db, group, cfg):
    rows = db.execute('SELECT text,images FROM events WHERE group_id=? ORDER BY created DESC LIMIT ?',
                      (group, cfg.get('context_messages', 12))).fetchall()
    words = Counter()
    for row in rows:
        words.update(re.findall(r'[A-Za-z][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,8}', row['text']))
    summary = {'recent_messages': len(rows), 'image_messages': sum(bool(json.loads(r['images'])) for r in rows),
               'topics': [word for word, _ in words.most_common(10)]}
    db.execute('INSERT OR REPLACE INTO memory VALUES (?,?,?)', (group, json.dumps(summary, ensure_ascii=False), time.time()))


def context(db, group, cfg):
    """Bounded prompt-ready context; kept local until a future explicit model integration."""
    group = str(group)
    memory_row = db.execute('SELECT summary FROM memory WHERE group_id=?', (group,)).fetchone()
    rows = db.execute('SELECT text FROM events WHERE group_id=? ORDER BY created DESC LIMIT ?',
                      (group, cfg.get('context_messages', 12))).fetchall()
    budget = cfg.get('context_char_budget', 3000)
    recent = []
    for row in rows:
        piece = row['text'][:min(500, budget)]
        if not piece:
            continue
        recent.append(piece)
        budget -= len(piece)
        if budget <= 0:
            break
    return {'profile': configured_groups(cfg).get(group, {}).get('preference', ''),
            'summary': json.loads(memory_row['summary']) if memory_row else {}, 'recent': list(reversed(recent))}


def ingest(db, event, cfg, now=None):
    now = time.time() if now is None else now
    groups = configured_groups(cfg)
    source = str(event.get('group_id', ''))
    if event.get('post_type') != 'message' or event.get('message_type') != 'group' or source not in groups:
        return 'ignored'
    if str(event.get('user_id', '')) == str(cfg['bot_id']):
        return 'self'
    message_id = event.get('message_id')
    if message_id is None or now - float(event.get('time', now)) > cfg.get('max_event_age_seconds', 300):
        return 'old'
    text, images = normalize(event)
    if MARK in text:
        return 'loop'
    fp = fingerprint(text, images)
    if cfg.get('interaction',{}).get('enabled'):
        memory=MarkdownMemory(cfg.get('context_dir',ROOT/'context'))
        try:memory.observe(event,cfg['bot_id'],now)
        finally:memory.close()
    with db:
        inserted = db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',
                              (source, str(message_id), fp, text, json.dumps(images), now)).rowcount
        if not inserted:
            return 'duplicate_event'
        activity=min(now,float(event.get('time',now)))
        db.execute('INSERT INTO group_activity VALUES (?,?) ON CONFLICT(group_id) DO UPDATE SET last_member_message=max(last_member_message,excluded.last_member_message)',(source,activity))
        db.execute('INSERT OR IGNORE INTO seen VALUES (?,?,?)', (source, fp, now))
        refresh_memory(db, source, cfg)
        social.enqueue(db,event,cfg,now)
        if not eligible(text, images, cfg):
            return 'context_only'
        # A repeated meme is acknowledged as seen, but never creates a second relay campaign.
        inserted = db.execute('INSERT OR IGNORE INTO candidates(fingerprint,source_group,text,images,created,status) VALUES (?,?,?,?,?,?)',
                              (fp, source, text, json.dumps(images), now, 'pending')).rowcount
        if not inserted:
            return 'duplicate_content'
        cid = db.execute('SELECT id FROM candidates WHERE fingerprint=?', (fp,)).fetchone()[0]
        route = cfg.get('routes', {}).get(source, [g for g in groups if g != source])
        for target in dict.fromkeys(str(x) for x in route):
            if target == source or target not in groups:
                continue
            if db.execute('SELECT 1 FROM seen WHERE group_id=? AND fingerprint=?', (target, fp)).fetchone():
                continue
            db.execute('INSERT OR IGNORE INTO deliveries(candidate_id,target_group,fingerprint,status,created,updated) VALUES (?,?,?,?,?,?)',
                       (cid, target, fp, 'pending', now, now))
    return 'candidate'


def prune(db, cfg, now=None):
    now = time.time() if now is None else now
    with db:
        db.execute('DELETE FROM events WHERE created<?', (now - cfg.get('raw_retention_hours', 48)*3600,))
        # Retain no more than the configured number of context events per group.
        for group in configured_groups(cfg):
            db.execute('DELETE FROM events WHERE group_id=? AND message_id NOT IN (SELECT message_id FROM events WHERE group_id=? ORDER BY created DESC LIMIT ?)',
                       (group, group, cfg.get('max_messages_per_group', 200)))
            refresh_memory(db, group, cfg)
        cutoff = now - cfg.get('dedupe_retention_days', 30)*86400
        db.execute('DELETE FROM seen WHERE created<?', (cutoff,))
        db.execute("UPDATE deliveries SET status='expired',updated=? WHERE status IN ('pending','approved') AND created<?", (now, now-cfg.get('candidate_ttl_hours', 24)*3600))
        db.execute("UPDATE candidates SET text='',images='[]' WHERE created<?", (now-cfg.get('raw_retention_hours',48)*3600,))
        # Uncertain deliveries stay in the ledger for manual reconciliation.
        db.execute("DELETE FROM candidates WHERE created<? AND id NOT IN (SELECT candidate_id FROM deliveries WHERE status='uncertain')", (cutoff,))
        db.execute("DELETE FROM deliveries WHERE created<? AND status!='uncertain'", (cutoff,))
        db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)', ('last_maintenance', str(now)))


def dispatch(db, cfg, api_cfg, now=None):
    if cfg.get('mode', 'preview') != 'live':
        return 0
    now = time.time() if now is None else now
    login = agent.onebot_call(api_cfg, 'get_login_info')
    if str(login.get('user_id')) != str(cfg['bot_id']):
        raise ValueError('bot 账号不一致')
    groups = configured_groups(cfg)
    jobs = db.execute("""SELECT d.*,c.source_group,c.text,c.images,c.kind FROM deliveries d
        JOIN candidates c ON c.id=d.candidate_id WHERE d.status='approved' AND c.status='approved'
        AND d.created<=? ORDER BY d.id LIMIT 50""", (now-cfg.get('hold_seconds',120),)).fetchall()
    sent = 0
    for job in jobs:
        target = job['target_group']
        if target not in groups or job['source_group'] not in groups:
            continue
        if not group_is_active(db,target,cfg,now):
            continue
        paused=db.execute('SELECT until_time FROM operator_pause WHERE group_id=?',(target,)).fetchone()
        if paused and paused[0]>now:continue
        if job['kind']=='reply' and now-job['created']>180:
            with db:db.execute("UPDATE deliveries SET status='expired',updated=? WHERE id=?",(now,job['id']))
            continue
        # Serialize rate-limit accounting and claims across workers/processes.
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM seen WHERE group_id=? AND fingerprint=?', (target,job['fingerprint'])).fetchone():
            db.execute("UPDATE deliveries SET status='already_seen',updated=? WHERE id=? AND status='approved'", (now,job['id']))
            db.commit()
            continue
        day = db.execute("SELECT count(*) FROM deliveries WHERE target_group=? AND status IN ('sent','uncertain') AND updated>?",(target,now-86400)).fetchone()[0]
        global_day = db.execute("SELECT count(*) FROM deliveries WHERE status IN ('sent','uncertain') AND updated>?",(now-86400,)).fetchone()[0]
        last = db.execute("SELECT max(updated) FROM deliveries WHERE target_group=? AND status IN ('sent','uncertain')",(target,)).fetchone()[0]
        cooldown=cfg.get('reply_cooldown_seconds',120) if job['kind']=='reply' else cfg.get('group_cooldown_seconds',3600)
        if day >= cfg.get('max_per_group_day',3) or global_day >= cfg.get('max_total_day',10) or last and now-last < cooldown:
            db.rollback()
            continue
        if not db.execute("UPDATE deliveries SET status='uncertain',updated=? WHERE id=? AND status='approved'",(now,job['id'])).rowcount:
            db.rollback()
            continue
        db.commit()
        source = groups[job['source_group']]['name']
        text=job['text'] if job['kind']=='reply' else MARK+source+'\n'+job['text']
        parts = [{'type':'text','data':{'text':text}}]
        if cfg.get('send_images', True):
            parts.extend({'type':'image','data':{'file':image['url']}} for image in json.loads(job['images']))
        try:
            result = agent.onebot_call(api_cfg,'send_group_msg',{'group_id':int(target),'message':parts})
            with db:
                db.execute("UPDATE deliveries SET status='sent',message_id=?,updated=? WHERE id=?",(str(result['message_id']),now,job['id']))
                db.execute('INSERT OR IGNORE INTO seen VALUES (?,?,?)',(target,job['fingerprint'],now))
                db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',(target,'bot:'+str(result['message_id']),job['fingerprint'],'[自己] '+text,job['images'],now))
            if cfg.get('interaction',{}).get('enabled'):
                memory=MarkdownMemory(cfg.get('context_dir',ROOT/'context'))
                try:memory.add_note('group',target,'自己最近发言：'+text)
                finally:memory.close()
            sent += 1
        except Exception as exc:
            with db:
                db.execute('UPDATE deliveries SET error=? WHERE id=?',(type(exc).__name__,job['id']))
            alerts.record(db,'delivery_'+type(exc).__name__,'群消息投递失败，结果已标记为待核实。')
            break
        if sent >= cfg.get('max_per_tick',1):
            break
    return sent


def set_candidate(db, cid, status):
    with db:
        if not db.execute('UPDATE candidates SET status=? WHERE id=?',(status,cid)).rowcount:
            raise ValueError('找不到候选')
        db.execute("UPDATE deliveries SET status=? WHERE candidate_id=? AND status IN ('pending','approved')",(status,cid))


def export_inbox(db,cfg,path):
    groups=configured_groups(cfg)
    cards=[]
    for row in db.execute('SELECT * FROM candidates ORDER BY id DESC LIMIT 50'):
        destinations=db.execute('SELECT target_group,status FROM deliveries WHERE candidate_id=?',(row['id'],)).fetchall()
        source=groups.get(row['source_group'],{}).get('name',row['source_group'])
        targets='、'.join(groups.get(d['target_group'],{}).get('name',d['target_group'])+' ('+d['status']+')' for d in destinations)
        media=''.join('<p><a rel="noreferrer" target="_blank" href="{}">查看素材图片</a></p>'.format(html.escape(i['url'],quote=True)) for i in json.loads(row['images']))
        cards.append('<article><small>#{id} · {status} · 来自 {source}</small><p>{text}</p>{media}<details><summary>目标群与投递状态</summary><p>{targets}</p></details></article>'.format(id=row['id'],status=html.escape(row['status']),source=html.escape(source),text=html.escape(row['text']).replace('\n','<br>'),media=media,targets=html.escape(targets)))
    counts={row[0]:row[1] for row in db.execute('SELECT status,count(*) FROM deliveries GROUP BY status')}
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>群间冲浪工作流</title><style>body{background:#f3f3ed;color:#202821;font:16px system-ui;max-width:900px;margin:40px auto;padding:20px}article{background:white;padding:24px;border-radius:16px;margin:18px 0}p{line-height:1.7;overflow-wrap:anywhere}small{color:#536654}h1{font-size:38px}</style><h1>🪨 群间冲浪工作流</h1><p>监听 '+str(len(groups))+' 个群 · '+html.escape(cfg.get('mode','preview'))+' 模式</p><p>最近讨论 → 图文候选 → 审核 → 跨群去重 → 限速投递</p><p>'+html.escape(json.dumps(counts,ensure_ascii=False))+'</p>'+(''.join(cards) or '<article>等待新的图文或链接消息。普通闲聊仅用于本机上下文。</article>')
    temp=path.with_suffix('.tmp')
    temp.write_text(page,encoding='utf-8');temp.replace(path)


def handler(db_path, cfg, secret):
    config_source=cfg
    def current_config(): return config_source() if callable(config_source) else config_source
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            if self.path != '/health':
                self.send_error(404); return
            self.send_response(200); self.end_headers()
            live_cfg=current_config()
            self.wfile.write(json.dumps({'ok':True,'mode':live_cfg.get('mode','preview'),'groups':len(configured_groups(live_cfg))}).encode())
        def do_POST(self):
            if self.path != '/events':
                self.send_error(404); return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=262144:
                    self.send_error(413); return
                body=self.rfile.read(n)
                signature='sha1='+hmac.new(secret.encode(),body,hashlib.sha1).hexdigest()
                if not hmac.compare_digest(self.headers.get('X-Signature',''),signature):
                    self.send_error(403); return
                event=json.loads(body)
                if not isinstance(event,dict): raise ValueError()
                with closing(connect(db_path)) as db:
                    result=ingest(db,event,current_config())
                    db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('last_event',str(time.time())))
                    db.commit()
                self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
            except (ValueError,TypeError,AttributeError):
                self.send_error(400)
    return Handler


def main():
    p=argparse.ArgumentParser(description='持续运行的 QQ 群间搬运 workflow')
    p.add_argument('--config',type=Path,default=ROOT/'workflow.json')
    s=p.add_subparsers(dest='command',required=True)
    for command in ['run','status','pending','tick','inbox']: s.add_parser(command)
    c=s.add_parser('context'); c.add_argument('group')
    for command in ['approve','reject']:
        c=s.add_parser(command); c.add_argument('id',type=int)
    args=p.parse_args()
    cfg=json.loads(args.config.read_text())
    api_cfg=json.loads((ROOT/'config.json').read_text())
    db_path=Path(cfg['database'])
    if not db_path.is_absolute(): db_path=args.config.resolve().parent/db_path
    db_path.parent.mkdir(parents=True,exist_ok=True)
    db=connect(db_path)
    try:
        if args.command=='status':
            for row in db.execute('SELECT status,count(*) FROM deliveries GROUP BY status'): print(row[0],row[1])
            print('监听群数：',len(configured_groups(cfg)),'模式：',cfg.get('mode','preview'))
        elif args.command=='pending':
            for row in db.execute("SELECT id,source_group,text,images FROM candidates WHERE status='pending' ORDER BY id DESC LIMIT 30"):
                print('#{} [{}] {} [图片{}]'.format(row['id'],row['source_group'],row['text'][:100],len(json.loads(row['images']))))
        elif args.command=='context': print(json.dumps(context(db,args.group,cfg),ensure_ascii=False,indent=2))
        elif args.command=='inbox': export_inbox(db,cfg,ROOT/'workflow-inbox.html')
        elif args.command in ['approve','reject']: set_candidate(db,args.id,'approved' if args.command=='approve' else 'rejected')
        elif args.command=='tick':
            prune(db,cfg); print('发送条数：',dispatch(db,cfg,api_cfg))
        elif args.command=='run':
            secret=agent.sender_token(api_cfg['sender'])
            if not secret: raise ValueError('缺少事件签名密钥')
            def current_config(): return json.loads(args.config.read_text())
            server=ThreadingHTTPServer(('127.0.0.1',cfg.get('port',8766)),handler(db_path,current_config,secret))
            server.daemon_threads=True
            stop=threading.Event()
            def stopped(*_): stop.set()
            signal.signal(signal.SIGTERM,stopped); signal.signal(signal.SIGINT,stopped)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            sessions=SessionPool()
            print('群间 workflow 已启动，监听127.0.0.1:{}，模式{}'.format(cfg.get('port',8766),cfg.get('mode','preview')),flush=True)
            try:
                while not stop.is_set():
                    try:
                        cfg=current_config()
                        prune(db,cfg)
                        social.tick(db,cfg,sessions)
                        dispatch(db,cfg,api_cfg)
                        checked=db.execute("SELECT value FROM metrics WHERE key='last_bridge_check'").fetchone()
                        if not checked or time.time()-float(checked[0])>=60:
                            try:
                                bridge=agent.onebot_call(api_cfg,'get_status')
                                if not bridge.get('online'):raise ValueError('Offline')
                            except Exception:
                                alerts.record(db,'qq_offline','QQ 接口不可用或账号离线；连接恢复后会补发此通知。')
                            with db:db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('last_bridge_check',str(time.time())))
                        alerts.flush(db,cfg,api_cfg)
                        export_inbox(db,cfg,ROOT/'workflow-inbox.html')
                        with db:
                            db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('last_tick',str(time.time())))
                    except Exception as exc:
                        print('工作周期失败：'+type(exc).__name__,flush=True)
                        alerts.record(db,'worker_'+type(exc).__name__,'后台工作周期发生异常，服务会在下一周期继续。')
                    stop.wait(cfg.get('tick_seconds',30))
            finally:
                if cfg.get('interaction',{}).get('enabled'):
                    memory=MarkdownMemory(cfg.get('context_dir',ROOT/'context'))
                    try:
                        sessions.flush_active(memory)
                        memory.flush(cfg['groups'])
                    finally:memory.close()
                server.shutdown(); server.server_close(); thread.join(timeout=5)
    finally: db.close()


if __name__=='__main__': main()
