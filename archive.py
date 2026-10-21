"""Append-only long-term conversation archive. Working-cache pruning never deletes this data."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import emoticons


def terms(text):
    result=set(re.findall(r'[A-Za-z0-9_]{2,}',text.lower()))
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        result.update(run[i:i+2] for i in range(len(run)-1))
        result.update(ch for ch in run if ch not in '你我他的是什么了啊吗那这说个有也就都不在和与及后前要还行好嗯哦对吧哈呢会能从')
    return result


class Archive:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.root/'archive.sqlite3',timeout=20)
        self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS messages(
          id INTEGER PRIMARY KEY,group_id TEXT,message_id TEXT,user_id TEXT,nickname TEXT,
          text TEXT,images TEXT,reply_to TEXT,mentions TEXT,created REAL,received REAL,raw_json TEXT,
          exported INTEGER DEFAULT 0,UNIQUE(group_id,message_id));
          CREATE INDEX IF NOT EXISTS message_group_time ON messages(group_id,created);
          CREATE TABLE IF NOT EXISTS visual_notes(group_id TEXT,message_id TEXT,media_key TEXT,description TEXT,
            UNIQUE(group_id,message_id,media_key,description));''')
        try:
            self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(group_id UNINDEXED,terms)')
            self.fts=True
        except sqlite3.OperationalError:self.fts=False
        self.db.commit()

    def close(self):self.db.close()

    def record(self,event,received=None):
        received=time.time() if received is None else received
        group=str(event.get('group_id',''));mid=str(event.get('message_id',''))
        if not group.isdigit() or not mid or mid=='None':return False
        parts=event.get('message') if isinstance(event.get('message'),list) else []
        text='\n'.join(str(p.get('data',{}).get('text','')) for p in parts if isinstance(p,dict) and p.get('type')=='text')
        labels=emoticons.describe(parts)
        if labels:text+='\n'+labels
        images=[p.get('data',{}) for p in parts if isinstance(p,dict) and p.get('type') in ('image','mface')]
        quote=next((str(p.get('data',{}).get('id','')) for p in parts if isinstance(p,dict) and p.get('type')=='reply'),'')
        mentions=[str(p.get('data',{}).get('qq','')) for p in parts if isinstance(p,dict) and p.get('type')=='at']
        sender=event.get('sender') if isinstance(event.get('sender'),dict) else {}
        name=str(sender.get('card') or sender.get('nickname') or event.get('user_id',''))
        created=float(event.get('time') or received)
        with self.db:
            inserted=self.db.execute('''INSERT OR IGNORE INTO messages(group_id,message_id,user_id,nickname,text,images,reply_to,mentions,created,received,raw_json)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(group,mid,str(event.get('user_id','')),name,text,json.dumps(images),quote,json.dumps(mentions),created,received,json.dumps(event,ensure_ascii=False))).rowcount
            if not inserted and not event.get('archive_origin'):
                existing=self.db.execute('SELECT raw_json FROM messages WHERE group_id=? AND message_id=?',(group,mid)).fetchone()
                if existing and json.loads(existing[0]).get('archive_origin')=='working_cache_backfill':
                    self.db.execute('UPDATE messages SET user_id=?,nickname=?,text=?,images=?,reply_to=?,mentions=?,created=?,raw_json=?,exported=0 WHERE group_id=? AND message_id=?',(str(event.get('user_id','')),name,text,json.dumps(images),quote,json.dumps(mentions),created,json.dumps(event,ensure_ascii=False),group,mid))
                    if self.fts:
                        ident=self.db.execute('SELECT id FROM messages WHERE group_id=? AND message_id=?',(group,mid)).fetchone()[0]
                        self.db.execute('DELETE FROM message_search WHERE rowid=?',(ident,))
                        self.db.execute('INSERT INTO message_search(rowid,group_id,terms) VALUES (?,?,?)',(ident,group,' '.join(terms(text+' '+name))))
            if inserted and self.fts:
                row=self.db.execute('SELECT id FROM messages WHERE group_id=? AND message_id=?',(group,mid)).fetchone()
                self.db.execute('INSERT INTO message_search(rowid,group_id,terms) VALUES (?,?,?)',(row['id'],group,' '.join(terms(text+' '+name))))
        self.flush_exports(group)
        return bool(inserted)

    def flush_exports(self,group=None):
        query='SELECT * FROM messages WHERE exported=0';params=[]
        if group is not None:query+=' AND group_id=?';params.append(group)
        query+=' ORDER BY id LIMIT 500'
        for row in self.db.execute(query,params).fetchall():
            month=datetime.fromtimestamp(row['created'],timezone.utc).strftime('%Y-%m')
            path=self.root/'groups'/row['group_id']/'history'/(month+'.jsonl');path.parent.mkdir(parents=True,exist_ok=True)
            entry={'event_key':row['group_id']+':'+row['message_id'],'received':row['received'],'event':json.loads(row['raw_json'])}
            # SQLite is authoritative. A crash between append and acknowledgement can duplicate
            # a JSONL event_key, but cannot lose the original durable record.
            with path.open('a',encoding='utf-8') as output:
                output.write(json.dumps(entry,ensure_ascii=False)+'\n');output.flush();os.fsync(output.fileno())
            with self.db:self.db.execute('UPDATE messages SET exported=1 WHERE id=?',(row['id'],))

    def window(self,group,since,limit=300,char_budget=30000):
        rows=self.db.execute('SELECT message_id,user_id,nickname,text,reply_to,mentions,created FROM messages WHERE group_id=? AND created>=? ORDER BY created DESC LIMIT ?',(str(group),since,limit)).fetchall()
        total=self.db.execute('SELECT count(*) FROM messages WHERE group_id=? AND created>=?',(str(group),since)).fetchone()[0]
        result=[];used=0
        for row in rows:
            item=dict(row);item['mentions']=json.loads(item['mentions'] or '[]');size=len(item['text'])
            if used+size>char_budget:break
            result.append(item);used+=size
        return {'messages':list(reversed(result)),'total_messages':total,'included_messages':len(result),'incomplete':len(result)<total}

    def get(self,group,message):
        row=self.db.execute('SELECT * FROM messages WHERE group_id=? AND message_id=?',(str(group),str(message))).fetchone()
        return dict(row) if row else None

    def add_visual(self,group,message,description,media_key=''):
        row=self.db.execute('SELECT id,text,nickname FROM messages WHERE group_id=? AND message_id=?',(str(group),str(message))).fetchone()
        if not row:return
        description=description[:1200]
        if media_key and not re.fullmatch(r'[a-f0-9]{64}\.jpg',media_key):media_key=''
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO visual_notes VALUES (?,?,?,?)',(str(group),str(message),media_key,description))
            if self.fts:
                captions=' '.join(r[0] for r in self.db.execute('SELECT description FROM visual_notes WHERE group_id=? AND message_id=?',(str(group),str(message))))
                self.db.execute('DELETE FROM message_search WHERE rowid=?',(row['id'],))
                self.db.execute('INSERT INTO message_search(rowid,group_id,terms) VALUES (?,?,?)',(row['id'],str(group),' '.join(terms(row['text']+' '+row['nickname']+' '+captions))))

    def search(self,group,query,before=None,limit=6):
        query_terms=sorted(terms(query),key=lambda value:(-len(value),value))[:24]
        if not query_terms:return []
        cutoff=time.time()+1 if before is None else before
        rows=[]
        if self.fts:
            expression=' OR '.join('"'+term.replace('"','""')+'"' for term in query_terms)
            rows=self.db.execute('''SELECT m.* FROM message_search JOIN messages m ON m.id=message_search.rowid
              WHERE message_search MATCH ? AND m.group_id=? AND m.created<? ORDER BY rank LIMIT ?''',
              (expression,str(group),cutoff,limit)).fetchall()
        else:
            condition=' OR '.join('text LIKE ?' for _ in query_terms)
            rows=self.db.execute('SELECT * FROM messages WHERE group_id=? AND created<? AND ('+condition+') ORDER BY created DESC LIMIT ?',
                                 [str(group),cutoff,*['%'+term+'%' for term in query_terms],limit]).fetchall()
        result=[]
        for row in rows:
            visuals=self.db.execute('SELECT media_key,description FROM visual_notes WHERE group_id=? AND message_id=?',(str(group),row['message_id'])).fetchall()
            result.append({'message_id':row['message_id'],'speaker':row['user_id'],'nickname':row['nickname'],
                           'time':row['created'],'text':row['text'][:1500],'reply_to':row['reply_to'],
                           'visual_notes':[dict(v) for v in visuals]})
        return result


def enabled(cfg):return cfg.get('durable_archive',{}).get('enabled',False)


def record_event(cfg,event,received=None):
    if not enabled(cfg):return
    archive=Archive(cfg['context_dir'])
    try:archive.record(event,received)
    finally:archive.close()


def record_visual(cfg,event_key,description,media_key=''):
    if not enabled(cfg):return
    group,message=event_key.split(':',1)
    archive=Archive(cfg['context_dir'])
    try:archive.add_visual(group,message,description,media_key)
    finally:archive.close()
