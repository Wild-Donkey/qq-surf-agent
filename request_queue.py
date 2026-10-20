"""Durable addressed requests, independent of the rolling chat window."""
import json,re,time

def ensure_schema(db):
 db.execute('''CREATE TABLE IF NOT EXISTS user_requests(event_key TEXT PRIMARY KEY,group_id TEXT,user_id TEXT,text TEXT,kind TEXT,target_group TEXT,status TEXT,created REAL,next_attempt REAL,delivery_id INTEGER,outcome TEXT,context_snapshot TEXT)''')
 if 'context_snapshot' not in {r[1] for r in db.execute('PRAGMA table_info(user_requests)')}:db.execute('ALTER TABLE user_requests ADD COLUMN context_snapshot TEXT')

def observe(db,event,cfg,text,now):
 ensure_schema(db);group=str(event['group_id']);user=str(event.get('user_id',''));key=group+':'+str(event['message_id'])
 profile=db.execute('SELECT kind FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
 if user==str(cfg.get('bot_id','')) or profile and profile[0]=='bot':return
 parts=event.get('message') if isinstance(event.get('message'),list) else [];addressed=any(p.get('type')=='at' and str(p.get('data',{}).get('qq'))==str(cfg.get('bot_id','')) for p in parts if isinstance(p,dict))
 addressed=addressed or text.strip().lower().startswith(('bot','luna')) or bool(re.search(r'你是谁|你有什么功能|你能做什么',text))
 if re.search(r'不用转|别转了|取消(?:转发|请求)|不要转了',text):
  ids=[r[0] for r in db.execute("SELECT delivery_id FROM user_requests WHERE group_id=? AND user_id=? AND status IN ('pending','queued')",(group,user)) if r[0] is not None]
  db.execute("UPDATE user_requests SET status='cancelled' WHERE group_id=? AND user_id=? AND status IN ('pending','queued')",(group,user))
  for delivery in ids:db.execute("UPDATE deliveries SET status='cancelled' WHERE id=? AND status='approved'",(delivery,))
  return
 forward=bool(re.search(r'合并转发|转发.{0,20}(?:到|给)|搬到|搬去',text))
 if not addressed and not forward:return
 target=None
 match=re.search(r'(?:搬到|搬去|转发到)(.{1,30}?)(?:给|让|看看|吧|[，。\n]|$)',text)
 if match:
  hint=match[1].strip();targets=[str(g['id']) for g in cfg['groups'] if hint and hint in g.get('name','')]
  if len(targets)==1:target=targets[0]
 snapshot=[dict(r) for r in db.execute('SELECT message_id,text,created FROM events WHERE group_id=? AND created<? ORDER BY created DESC LIMIT 20',(group,now))]
 snapshot.reverse()
 db.execute("INSERT OR IGNORE INTO user_requests VALUES (?,?,?,?,?,?,'pending',?,?,NULL,NULL,?)",(key,group,user,text[:2000],'forward' if forward else 'reply',target,now,now,json.dumps(snapshot,ensure_ascii=False)))

def pending(db,group):
 rows=[dict(r) for r in db.execute("SELECT event_key,user_id,text,kind,target_group,created,context_snapshot FROM user_requests WHERE group_id=? AND status='pending' ORDER BY created LIMIT 8",(str(group),))]
 for index,row in enumerate(rows):
  snapshot=json.loads(row.pop('context_snapshot') or '[]')
  row['original_context']=[{**r,'text':r['text'][:350]} for r in snapshot] if index==0 else []
 return rows


def sync(db,cfg,now=None):
 import participants
 now=time.time() if now is None else now
 with db:
  for row in db.execute("SELECT r.event_key,d.status FROM user_requests r JOIN deliveries d ON d.id=r.delivery_id WHERE r.status='queued'").fetchall():
   state={'sent':'done','already_seen':'done','uncertain':'uncertain','failed':'failed','cancelled':'cancelled','superseded':'pending','expired':'pending'}.get(row['status'])
   if state:db.execute('UPDATE user_requests SET status=?,next_attempt=? WHERE event_key=?',(state,now+5,row['event_key']))
  for row in db.execute("SELECT event_key,group_id FROM user_requests WHERE status='pending' AND next_attempt<=?",(now,)).fetchall():
   pause=db.execute('SELECT until_time FROM operator_pause WHERE group_id=?',(row['group_id'],)).fetchone()
   if pause and pause[0]>now:continue
   if participants.humans_recent(db,row['group_id'],cfg,now):
    db.execute("UPDATE social_jobs SET status='pending' WHERE event_key=? AND target_group=? AND status='decided'",(row['event_key'],row['group_id']))

def accepts(db,group,decision,requests):
 ids=decision.get('request_ids',[]);outcome=decision.get('request_outcome')
 if not requests:return []
 if not isinstance(ids,list) or not ids or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):return []
 allowed={r['event_key']:r for r in requests}
 if any(x not in allowed for x in ids) or outcome not in ('completed','declined','needs_info'):return []
 if decision['action']=='wait':return []
 if any(allowed[key]['kind']=='reply' for key in ids) and outcome=='completed' and decision['action']!='reply':return []
 for key in ids:
  if not db.execute("SELECT 1 FROM user_requests WHERE event_key=? AND group_id=? AND status='pending'",(key,str(group))).fetchone():return []
  if allowed[key]['kind']=='forward' and outcome=='completed' and decision['action'] not in ('merged_forward','share_image'):return []
 if outcome in ('declined','needs_info') and (decision['action']!='reply' or not decision.get('text','').strip()):return []
 return ids

def bind(db,group,ids,decision,delivery,now):
 if not ids or delivery is None:return
 row=db.execute('SELECT target_group FROM deliveries WHERE id=?',(delivery,)).fetchone()
 if not row:return
 with db:
  requested=[db.execute('SELECT * FROM user_requests WHERE event_key=? AND group_id=?',(key,str(group))).fetchone() for key in ids]
  if any(r['target_group'] and decision['request_outcome']=='completed' and row['target_group']!=r['target_group'] for r in requested):
   db.execute("UPDATE deliveries SET status='cancelled' WHERE id=? AND status='approved'",(delivery,));return
  for key in ids:
   request=db.execute('SELECT * FROM user_requests WHERE event_key=? AND group_id=?',(key,str(group))).fetchone()
   db.execute("UPDATE user_requests SET status='queued',delivery_id=?,outcome=? WHERE event_key=? AND status='pending'",(delivery,decision['request_outcome'],key))

def is_bound(db,delivery):
 return bool(db.execute("SELECT 1 FROM user_requests WHERE delivery_id=? AND status='queued'",(delivery,)).fetchone())
