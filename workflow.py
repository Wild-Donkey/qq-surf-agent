#!/usr/bin/env python3
"""Persistent group-to-group relay. No third-party dependencies or external LLM calls."""
import archive
import moderation
import emoticons
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
import usage_report
import onboarding
import participants
from memory import MarkdownMemory
from sessions import SessionPool

ROOT = Path(__file__).resolve().parent
MARK = '群间搬运｜'


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
    if 'caption' not in {r[1] for r in db.execute('PRAGMA table_info(deliveries)')}:
        db.execute('ALTER TABLE deliveries ADD COLUMN caption TEXT')
    if 'context_watermark' not in {r[1] for r in db.execute('PRAGMA table_info(deliveries)')}:
        db.execute('ALTER TABLE deliveries ADD COLUMN context_watermark REAL')
    if 'emoticon' not in {r[1] for r in db.execute('PRAGMA table_info(candidates)')}:
        db.execute('ALTER TABLE candidates ADD COLUMN emoticon TEXT')
    if 'deadline_reply' not in {r[1] for r in db.execute('PRAGMA table_info(deliveries)')}:
        db.execute('ALTER TABLE deliveries ADD COLUMN deadline_reply INTEGER DEFAULT 0')
    moderation.ensure_schema(db)
    social.ensure_schema(db)
    alerts.ensure_schema(db)
    participants.ensure_schema(db)
    db.commit()
    return db


def configured_groups(cfg):
    return {str(x['id']): x for x in cfg['groups'] if x.get('enabled', True)}


def group_is_active(db,group,cfg,now=None):
    now=time.time() if now is None else now
    if moderation.is_muted(db,group,now):return False
    row=db.execute('SELECT last_member_message FROM group_activity WHERE group_id=?',(str(group),)).fetchone()
    return bool(row and 0<=now-row[0]<=cfg.get('inactive_group_days',14)*86400)


def newer_context(db,group,watermark):
    return watermark is not None and bool(db.execute("SELECT 1 FROM events WHERE group_id=? AND created>? AND message_id NOT LIKE 'bot:%' LIMIT 1",(group,watermark)).fetchone())


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
        elif part.get('type') in ('image','mface'):
            url = str(data.get('url', ''))
            if urlsplit(url).scheme == 'https':
                # Never accept arbitrary local files or CQ instructions from source messages.
                images.append({'url': url, 'key': str(data.get('file') or urlsplit(url).path)})
    labels=emoticons.describe(parts)
    if labels:text.append(labels)
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


def _register_and_backfill_group(db, group_id, cfg, api_cfg, now):
    """Add a newly joined group to config and ingest its recent messages so the bot can reply."""
    if not group_id or not group_id.isdigit():
        return
    configured = configured_groups(cfg)
    if group_id not in configured:
        try:
            path = ROOT / 'workflow.json'
            current = json.loads(path.read_text())
            if not any(str(g.get('id')) == group_id for g in current.get('groups', [])):
                new_group = {'id': group_id, 'name': group_id, 'enabled': True, 'preference': cfg.get('default_group_preference', '有趣的图文和链接；避免搬运日常对话片段')}
                current['groups'].append(new_group)
                path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n')
                # Update in-memory cfg so subsequent ingest calls in this tick see the new group.
                cfg.setdefault('groups', []).append(new_group)
                alerts.record(db, 'group_auto_registered', '新群已自动加入配置。', scope=group_id, details={'group_id': group_id})
                # Mark for onboarding so the bot introduces itself after scanning.
                db.execute('CREATE TABLE IF NOT EXISTS group_onboarding(group_id TEXT PRIMARY KEY,created REAL,status TEXT,new_group INTEGER,intro_candidate INTEGER,retry_after REAL DEFAULT 0)')
                db.execute('INSERT OR IGNORE INTO group_onboarding(group_id,created,status,new_group) VALUES (?,?,?,?)',(group_id,now,'pending',1))
        except Exception as exc:
            alerts.record(db, 'group_register_failed', '新群写入配置失败。', scope=group_id, details={'error': type(exc).__name__})
            return
    # Backfill recent messages; only recent ones go through the social pipeline.
    try:
        history = agent.onebot_call(api_cfg, 'get_group_msg_history', {'group_id': int(group_id), 'count': 100, 'disable_get_url': True})
        max_age = cfg.get('max_event_age_seconds', 300)
        count = 0
        for event in history.get('messages', []):
            event = dict(event, post_type='message', message_type='group', group_id=group_id)
            stamp = float(event.get('time', now))
            if now - stamp > max_age:
                continue
            if ingest(db, event, cfg, now, api_cfg) == 'candidate':
                count += 1
        if count:
            alerts.record(db, 'group_backfilled', '新群已回溯 {} 条近期消息。'.format(count), scope=group_id, details={'group_id': group_id, 'count': count})
    except Exception as exc:
        alerts.record(db, 'group_backfill_failed', '新群回溯消息失败。', scope=group_id, details={'error': type(exc).__name__, 'group_id': group_id})


def ingest(db, event, cfg, now=None, api_cfg=None):
    now = time.time() if now is None else now
    if moderation.ingest(db,event,cfg,now):return "moderation_notice"
    # Auto-accept group invitations directed at this bot (not member apply requests).
    if event.get('post_type') == 'request' and event.get('request_type') == 'group':
        sub = event.get('sub_type')
        if sub == 'invite' and cfg.get('auto_accept_group_invite', False) and api_cfg is not None:
            flag = event.get('flag')
            if flag:
                try:
                    agent.onebot_call(api_cfg, 'set_group_add_request', {'flag': flag, 'sub_type': 'invite', 'approve': True})
                    alerts.record(db, 'group_invite_accepted', '已自动接受进群邀请。', scope=str(event.get('group_id','')), details={'group_id': event.get('group_id'), 'user_id': event.get('user_id')})
                    # Immediately register the new group and backfill recent messages so the bot can reply.
                    _register_and_backfill_group(db, str(event.get('group_id','')), cfg, api_cfg, now)
                    return 'invite_accepted'
                except Exception as exc:
                    alerts.record(db, 'group_invite_failed', '自动接受进群邀请失败。', scope=str(event.get('group_id','')), details={'error': type(exc).__name__, 'group_id': event.get('group_id')})
                    return 'invite_failed'
        return 'ignored_request'
    groups = configured_groups(cfg)
    source = str(event.get('group_id', ''))
    if event.get('post_type') != 'message' or event.get('message_type') != 'group' or source not in groups:
        return 'ignored'
    archive.record_event(cfg,event,now)
    if str(event.get('user_id', '')) == str(cfg['bot_id']):
        with db:participants.observe(db,event,cfg,normalize(event)[0],now)
        return 'self'
    message_id = event.get('message_id')
    if message_id is None or now - float(event.get('time', now)) > cfg.get('max_event_age_seconds', 300):
        return 'old'
    text, images = normalize(event)
    if any(isinstance(p,dict) and p.get('type')=='at' and str(p.get('data',{}).get('qq'))==str(cfg['bot_id']) for p in (event.get('message') if isinstance(event.get('message'),list) else [])):
        text='[@你] '+text
    if MARK in text:
        return 'loop'
    fp = fingerprint(text, images)
    if cfg.get('interaction',{}).get('enabled'):
        memory=MarkdownMemory(cfg.get('context_dir',ROOT/'context'))
        try:memory.observe(event,cfg['bot_id'],now)
        finally:memory.close()
    with db:
        participants.observe(db,event,cfg,text,now)
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
        participants.prune(db,now)
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
    moderation.flush(db,cfg,api_cfg,now)
    login = agent.onebot_call(api_cfg, 'get_login_info')
    if str(login.get('user_id')) != str(cfg['bot_id']):
        raise ValueError('bot 账号不一致')
    groups = configured_groups(cfg)
    jobs = db.execute("""SELECT d.*,c.source_group,c.text,c.images,c.kind,c.emoticon FROM deliveries d
        JOIN candidates c ON c.id=d.candidate_id WHERE d.status='approved' AND c.status='approved'
        AND ((c.kind IN ('reply','merged_forward','moment_forward','image_share') AND d.created<=?) OR (c.kind NOT IN ('reply','merged_forward','moment_forward','image_share') AND d.created<=?))
        ORDER BY CASE WHEN c.kind='reply' THEN 0 ELSE 1 END,d.id LIMIT 50""", (now-cfg.get('reply_delay_seconds',0),now-cfg.get('hold_seconds',120))).fetchall()
    sent = 0
    for job in jobs:
        target = job['target_group']
        if target not in groups or job['source_group'] not in groups:
            continue
        if job['kind']=='reply' and str(job['source_group'])!=str(target):
            with db:db.execute("UPDATE deliveries SET status='blocked_cross_group_reply' WHERE id=?",(job['id'],))
            continue
        if not group_is_active(db,target,cfg,now):
            continue
        if not participants.humans_recent(db,target,cfg,now):continue
        if cfg.get('refresh_before_send',False):
            try:
                history=agent.onebot_call(api_cfg,'get_group_msg_history',{'group_id':int(target),'count':12,'disable_get_url':True})
                for event in history.get('messages',[]):
                    event=dict(event,post_type='message',message_type='group',group_id=target)
                    ingest(db,event,cfg)
            except Exception as exc:
                alerts.record(db,'refresh_before_send_failed','发送前刷新群消息失败，暂不发旧草稿。',details={'stage':'get_group_msg_history','error':type(exc).__name__,'retcode':getattr(exc,'retcode',None)})
                continue
        # Only a frozen continuous-traffic window may ignore newer arrivals.
        if job['kind'] not in ('moment_forward','image_share') and not social.request_queue.is_bound(db,job['id']) and not job['deadline_reply'] and newer_context(db,target,job['context_watermark']):
            with db:db.execute("UPDATE deliveries SET status='superseded',updated=? WHERE id=?",(now,job['id']))
            continue
        paused=db.execute('SELECT until_time FROM operator_pause WHERE group_id=?',(target,)).fetchone()
        if paused and paused[0]>now:continue
        if job['kind'] in ('reply','merged_forward','moment_forward','image_share') and now-job['created']>180:
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
        cooldown=cfg.get('reply_cooldown_seconds',0) if job['kind']=='reply' else cfg.get('group_cooldown_seconds',3600)
        if job['kind'] in ('merged_forward','moment_forward','image_share'):cooldown=cfg.get('merged_forward',{}).get('cooldown_seconds',60)
        if job['kind']!='reply':
            last=db.execute("SELECT max(d.updated) FROM deliveries d JOIN candidates c ON c.id=d.candidate_id WHERE d.target_group=? AND c.kind!='reply' AND d.status IN ('sent','uncertain')",(target,)).fetchone()[0]
            shares=db.execute("SELECT count(*) FROM deliveries d JOIN candidates c ON c.id=d.candidate_id WHERE d.target_group=? AND c.kind!='reply' AND d.status IN ('sent','uncertain') AND d.updated>?",(target,now-86400)).fetchone()[0]
            share_cap=cfg.get('max_shares_per_group_day',3)
            if share_cap is not None and shares>=share_cap:
                db.rollback();continue
        group_cap=cfg.get('max_per_group_day',3);total_cap=cfg.get('max_total_day',10)
        if (group_cap is not None and day>=group_cap) or (total_cap is not None and global_day>=total_cap) or last and now-last < cooldown:
            db.rollback()
            continue
        if not db.execute("UPDATE deliveries SET status='uncertain',updated=? WHERE id=? AND status='approved'",(now,job['id'])).rowcount:
            db.rollback()
            continue
        db.commit()
        source = groups[job['source_group']]['name']
        text=job['text'] if job['kind']=='reply' else MARK+source+'\n'+(job['caption'] or job['text'])
        parts = [{'type':'text','data':{'text':text}}] if text else []
        if job['emoticon']:parts.extend(emoticons.segments(cfg['context_dir'],target,json.loads(job['emoticon'])))
        if cfg.get('send_images', True):
            parts.extend({'type':'image','data':{'file':image['url']}} for image in json.loads(job['images']))
        try:
            if job['kind'] not in ('moment_forward','image_share') and not social.request_queue.is_bound(db,job['id']) and not job['deadline_reply'] and newer_context(db,target,job['context_watermark']):
                with db:db.execute("UPDATE deliveries SET status='superseded',updated=? WHERE id=?",(time.time(),job['id']))
                continue
            if not participants.humans_recent(db,target,cfg):
                with db:db.execute("UPDATE deliveries SET status='approved' WHERE id=?",(job['id'],))
                continue
            if job['kind']=='image_share':
                import image_sharing
                parts=image_sharing.send_parts(db,cfg,job['source_group'],target,job['text'])
                result=agent.onebot_call(api_cfg,'send_group_msg',{'group_id':int(target),'message':parts})
                text='[分享图片：'+json.loads(job['text'])['description'][:200]+']'
            elif job['kind'] in ('merged_forward','moment_forward'):
                import merged_forward
                forward_payload=merged_forward.send_payload(db,cfg,target,job['source_group'],job['text'])
                result=agent.onebot_call(api_cfg,'send_group_forward_msg',forward_payload)
                text='[合并转发：'+source+'，'+str(len(forward_payload['messages']))+'条]'
                parts=[{'type':'text','data':{'text':text}}]
            elif job['kind']=='forward':
                mid=str(job['text']).strip()
                if not db.execute('SELECT 1 FROM events WHERE group_id=? AND message_id=?',(target,mid)).fetchone():
                    raise ValueError('forward_message_outside_group')
                result=agent.onebot_call(api_cfg,'send_group_forward_msg',{'group_id':int(target),'messages':[{'type':'node','data':{'id':mid}}]})
                parts=[{'type':'text','data':{'text':'[转发消息 '+mid+']'}}]
                text='[转发消息 '+mid+']'
            else:
                result = agent.onebot_call(api_cfg,'send_group_msg',{'group_id':int(target),'message':parts})
            archive.record_event(cfg,{'group_id':target,'message_id':str(result['message_id']),'user_id':cfg['bot_id'],'time':now,'message':parts})
            with db:
                db.execute("UPDATE deliveries SET status='sent',message_id=?,updated=? WHERE id=?",(str(result['message_id']),now,job['id']))
                db.execute('INSERT OR IGNORE INTO seen VALUES (?,?,?)',(target,job['fingerprint'],now))
                db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',(target,'bot:'+str(result['message_id']),job['fingerprint'],'[自己] '+text,job['images'],now))
            if cfg.get('interaction',{}).get('enabled'):
                memory=MarkdownMemory(cfg.get('context_dir',ROOT/'context'))
                try:memory.add_note('group',target,'自己最近发言：'+text)
                finally:memory.close()
            if job['emoticon']:
                emoticons.mark_used(cfg['context_dir'],target,json.loads(job['emoticon']))
            sent += 1
        except Exception as exc:
            with db:
                db.execute('UPDATE deliveries SET error=? WHERE id=?',(type(exc).__name__,job['id']))
            alerts.record(db,'delivery_'+type(exc).__name__,'群消息投递失败，结果已标记为待核实。',scope=str(target),details={'target_group':str(target),'delivery_id':job['id'],'kind':job['kind'],'error':type(exc).__name__,'retcode':getattr(exc,'retcode',None)})
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
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>群间冲浪工作流</title><style>body{background:#f3f3ed;color:#202821;font:16px system-ui;max-width:900px;margin:40px auto;padding:20px}article{background:white;padding:24px;border-radius:16px;margin:18px 0}p{line-height:1.7;overflow-wrap:anywhere}small{color:#536654}h1{font-size:38px}</style><h1>群间冲浪工作流</h1><p>监听 '+str(len(groups))+' 个群 · '+html.escape(cfg.get('mode','preview'))+' 模式</p><p>最近讨论 → 图文候选 → 审核 → 跨群去重 → 限速投递</p><p>'+html.escape(json.dumps(counts,ensure_ascii=False))+'</p>'+(''.join(cards) or '<article>等待新的图文或链接消息。普通闲聊仅用于本机上下文。</article>')
    temp=path.with_suffix('.tmp')
    temp.write_text(page,encoding='utf-8');temp.replace(path)


def handler(db_path, cfg, secret, api_loader=None):
    config_source=cfg
    def current_config(): return config_source() if callable(config_source) else config_source
    def current_api(): return api_loader() if callable(api_loader) else (api_loader if api_loader else None)
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
                self.connection.settimeout(10)
                if self.headers.get('Transfer-Encoding','').lower()=='chunked':
                    if self.headers.get('Content-Length') is not None:raise ValueError('Ambiguous framing')
                    chunks=[];total=0
                    while True:
                        line=self.rfile.readline(128)
                        if not line.endswith(b'\r\n'):raise ValueError('Invalid chunk header')
                        n=int(line[:-2].split(b';',1)[0],16)
                        if n<0 or total+n>262144:self.send_error(413);return
                        if not n:
                            if self.rfile.readline(8192)!=b'\r\n':raise ValueError('Unsupported trailers')
                            break
                        chunk=self.rfile.read(n)
                        if len(chunk)!=n or self.rfile.read(2)!=b'\r\n':raise ValueError('Incomplete chunk')
                        chunks.append(chunk);total+=n
                    body=b''.join(chunks)
                    if not body:raise ValueError('Empty body')
                elif self.headers.get('Transfer-Encoding'):
                    raise ValueError('Unsupported transfer encoding')
                else:
                    n=int(self.headers.get('Content-Length','0'))
                    if not 0<n<=262144:
                        self.send_error(413); return
                    body=self.rfile.read(n)
                    if len(body)!=n:raise ValueError('Incomplete body')
                signature='sha1='+hmac.new(secret.encode(),body,hashlib.sha1).hexdigest()
                if not hmac.compare_digest(self.headers.get('X-Signature',''),signature):
                    self.send_error(403); return
                event=json.loads(body)
                if not isinstance(event,dict): raise ValueError()
                # DEBUG: log all incoming events to capture invite/private message shapes
                try:
                    with open(ROOT/'runtime'/'raw_events.log','a') as _f:
                        _f.write(json.dumps(event,ensure_ascii=False)[:2000]+'\n')
                except Exception:
                    pass
                with closing(connect(db_path)) as db:
                    result=ingest(db,event,current_config(),api_cfg=current_api())
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
            def current_api(): return json.loads((ROOT/'config.json').read_text())
            server=ThreadingHTTPServer(('127.0.0.1',cfg.get('port',8766)),handler(db_path,current_config,secret,current_api))
            server.daemon_threads=True
            stop=threading.Event()
            def stopped(*_): stop.set()
            signal.signal(signal.SIGTERM,stopped); signal.signal(signal.SIGINT,stopped)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            if cfg.get('memory_schema',{}).get('enabled',False):
                import memory_store
                memory_store.migrate(cfg['context_dir'],cfg['groups'],[dict(row) for row in db.execute('SELECT * FROM speaker_profiles')])
            with db:db.execute("UPDATE social_jobs SET status='pending' WHERE status='processing'")
            participants.reconcile_archive(db,cfg)
            participants.sync_config(db,cfg)
            # Migration: existing cached messages still count toward human presence.
            for prior in db.execute('SELECT e.*,j.user_id FROM events e JOIN social_jobs j ON j.event_key=e.group_id||":"||e.message_id AND j.target_group=e.group_id WHERE e.created>?',(time.time()-300,)).fetchall():
                with db:participants.observe(db,{'group_id':prior['group_id'],'message_id':prior['message_id'],'user_id':prior['user_id'],'time':prior['created']},cfg,prior['text'],time.time())
            # Recover messages arriving during a restart, without replaying old conversations.
            for group in configured_groups(cfg):
                if not group_is_active(db,group,cfg):continue
                try:
                    history=agent.onebot_call(api_cfg,'get_group_msg_history',{'group_id':int(group),'count':100,'disable_get_url':True})
                    for event in history.get('messages',[]):
                        event=dict(event,post_type='message',message_type='group',group_id=group)
                        archive.record_event(cfg,event)
                        if time.time()-float(event.get('time',0))<=180:ingest(db,event,cfg)
                except Exception as exc:
                    alerts.record(db,'startup_history_failed','重启时补读群消息失败。',scope=group,details={'error':type(exc).__name__})
            sessions=SessionPool()
            print('群间 workflow 已启动，监听127.0.0.1:{}，模式{}'.format(cfg.get('port',8766),cfg.get('mode','preview')),flush=True)
            try:
                while not stop.is_set():
                    try:
                        cfg=current_config()
                        prune(db,cfg)
                        usage_report.tick(db,cfg)
                        social.tick(db,cfg,sessions)
                        dispatch(db,cfg,api_cfg)
                        cfg=onboarding.tick(db,cfg,api_cfg,args.config)
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
