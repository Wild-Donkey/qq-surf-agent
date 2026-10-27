"""Scoped QQ interactions: member resolution, quotes, and durable poke notices."""
import hashlib,json,re,sqlite3,time
from pathlib import Path
from app import identities

def member(cfg,group,alias,db=None,raw_id=False):
 registry=identities.Registry(cfg['context_dir'])
 try:user=str(alias) if raw_id else registry.resolve(alias)
 finally:registry.close()
 if not user.isdigit() or user in ('0',str(cfg.get('bot_id',''))) or str(group).startswith('private:'):raise ValueError('interaction_target_invalid')
 found=db.execute('SELECT 1 FROM speaker_events WHERE group_id=? AND user_id=? LIMIT 1',(str(group),user)).fetchone() if db is not None else None
 path=Path(cfg['context_dir'])/'archive.sqlite3'
 if not found and path.exists():
  disk=sqlite3.connect(path)
  try:found=disk.execute('SELECT 1 FROM messages WHERE group_id=? AND user_id=? LIMIT 1',(str(group),user)).fetchone()
  finally:disk.close()
 if not found:raise ValueError('interaction_member_outside_group')
 return user

def quote(cfg,group,mid,db=None):
 mid=str(mid).removeprefix('bot:')
 # Tolerate event_key format "group_id:message_id" by taking the last segment.
 if ':' in mid:mid=mid.rsplit(':',1)[-1]
 if not re.fullmatch(r'-?\d{1,30}',mid):raise ValueError('invalid_quote_message')
 found=None
 if db is not None:
  if str(group).startswith('private:'):found=db.execute('SELECT 1 FROM private_messages WHERE user_id=? AND message_id IN (?,?)',(str(group).split(':',1)[1],mid,'bot:'+mid)).fetchone()
  else:found=db.execute('SELECT 1 FROM events WHERE group_id=? AND message_id IN (?,?)',(str(group),mid,'bot:'+mid)).fetchone()
 path=Path(cfg['context_dir'])/'archive.sqlite3'
 if not found and not str(group).startswith('private:') and path.exists():
  disk=sqlite3.connect(path)
  try:found=disk.execute('SELECT 1 FROM messages WHERE group_id=? AND message_id=?',(str(group),mid)).fetchone()
  finally:disk.close()
 if not found:raise ValueError('quote_outside_conversation')
 return mid

def notice(event,cfg,now):
 if event.get('post_type')!='notice' or event.get('notice_type')!='notify' or event.get('sub_type')!='poke':return None
 sender=str(event.get('sender_id',event.get('user_id','')));target=str(event.get('target_id',''))
 if target!=str(cfg['bot_id']) or sender==str(cfg['bot_id']) or not sender.isdigit():return False
 group=event.get('group_id');when=float(event.get('time',now))
 if now-when>cfg.get('max_event_age_seconds',300) or when>now+30:return False
 digest=hashlib.sha256(json.dumps([str(group or ''),sender,target,when],ensure_ascii=False).encode()).hexdigest()[:24]
 return {'post_type':'message','message_type':'group' if group else 'private','sub_type':'friend','group_id':group,'user_id':sender,'message_id':'poke:'+digest,'time':when,'message':[{'type':'text','data':{'text':'[交互通知：对方戳了你一下；这不是对方说的话]'}}],'interaction':{'kind':'poke','actor':sender,'target':target},'original_notice':event}

def enqueue(db,cfg,job,decision,now):
 group=str(job['target_group']);user=member(cfg,group,decision.get('alias',''),db)
 recent=db.execute("SELECT 1 FROM candidates WHERE source_group=? AND kind='poke' AND text=? AND created>? LIMIT 1",(group,user,now-60)).fetchone()
 if recent:decision.update(action='wait',reason='同一成员戳一戳冷却中');return
 fp=hashlib.sha256(('poke:'+group+':'+str(job['event_key'])+':'+user).encode()).hexdigest()
 with db:
  db.execute("INSERT OR IGNORE INTO candidates(fingerprint,source_group,text,images,created,status,kind) VALUES (?,?,?,?,?,'approved','poke')",(fp,group,user,'[]',now))
  cid=db.execute('SELECT id FROM candidates WHERE fingerprint=?',(fp,)).fetchone()[0]
  db.execute("INSERT OR IGNORE INTO deliveries(candidate_id,target_group,fingerprint,status,created,updated,context_watermark,deadline_reply) VALUES (?,?,?,'approved',?,?,?,?)",(cid,group,fp,now,now,decision.get('_context_watermark',now),int(bool(decision.get('_deadline_reply')))))
 return db.execute('SELECT id FROM deliveries WHERE candidate_id=? AND target_group=?',(cid,group)).fetchone()[0]
