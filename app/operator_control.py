"""Durable local operator prompts; workers cannot choose another recipient."""
import json,time,uuid
from app import service_log

def ensure_schema(db):
 db.execute('''CREATE TABLE IF NOT EXISTS operator_prompts(
 id TEXT PRIMARY KEY,group_id TEXT,text TEXT,status TEXT,created REAL,updated REAL,error TEXT)''');db.commit()

def submit(db,cfg,group,text,now=None):
 now=time.time() if now is None else now;group=str(group);text=text.strip()
 if group not in {str(g['id']) for g in cfg['groups'] if g.get('enabled',True)}:raise ValueError('请选择已配置的群')
 if not 1<=len(text)<=12000:raise ValueError('prompt 长度应为1到12000字')
 ident=uuid.uuid4().hex[:16]
 with db:
  db.execute("INSERT INTO operator_prompts VALUES (?,?,?,'queued',?,?,NULL)",(ident,group,text,now,now))
  db.execute("""INSERT INTO social_jobs(event_key,target_group,source_group,user_id,created,status,trigger_text,trigger_images,trigger_media,trigger_reply,trigger_mentions)
    VALUES (?,?,?,?,?,'pending',?,'[]','[]','','[]')""",('operator:'+ident,group,group,str(cfg.get('alerts',{}).get('owner_qq','')),now,'[本机运营指令，请读取 operator_prompts]'))
 service_log.event('surf-bot','operator prompt queued id='+ident+' group='+group)
 return ident

def pending(db,group):
 result=[];budget=12000
 for row in db.execute("SELECT id,text FROM operator_prompts WHERE group_id=? AND status='queued' ORDER BY created LIMIT 8",(str(group),)):
  if result and len(row['text'])>budget:break
  result.append(dict(row));budget-=len(row['text'])
 return result

def complete(db,items,group,action):
 with db:
  for item in items:db.execute("UPDATE operator_prompts SET status='applied',updated=? WHERE id=? AND group_id=? AND status='queued'",(time.time(),item['id'],str(group)))
 with db:
  for row in db.execute("SELECT id FROM operator_prompts WHERE group_id=? AND status='queued'",(str(group),)).fetchall():
   db.execute("UPDATE social_jobs SET status='pending' WHERE event_key=? AND target_group=? AND status='decided'",('operator:'+row['id'],str(group)))
 for item in items:service_log.event('surf-bot','operator prompt applied id='+item['id']+' action='+action)


def wake(db):
 with db:
  for row in db.execute("SELECT id,group_id FROM operator_prompts WHERE status='queued'").fetchall():
   db.execute("UPDATE social_jobs SET status='pending' WHERE event_key=? AND target_group=? AND status='decided'",('operator:'+row['id'],row['group_id']))


def owner_message(db,cfg,group,message_id,text,image=None):
 import social,participants
 owner=str(cfg.get('alerts',{}).get('owner_qq',''))
 if not owner.isdigit():return
 now=time.time();event={'group_id':str(group),'user_id':owner,'message_id':str(message_id),'time':now,'message':[{'type':'text','data':{'text':'[bot owner 人工发言] '+text}}]}
 with db:
  participants.observe(db,dict(event,message_id='operator:'+str(message_id)),cfg,text,now)
  participants.set_profile(db,owner,'human',1,'本机运营者人工发言','operator',now)
  social.enqueue(db,event,cfg,now)
 if image is not None:
  import vision,hashlib
  from pathlib import Path
  preview,_=vision.decode_image(image);key=hashlib.sha256(preview).hexdigest()+'.jpg'
  directory=Path(cfg['context_dir'])/'media';directory.mkdir(parents=True,exist_ok=True)
  (directory/key).write_bytes(preview)
  with db:db.execute('UPDATE social_jobs SET trigger_images=1,trigger_media=? WHERE event_key=? AND target_group=?',(json.dumps([{'cached_asset_key':key}]),str(group)+':'+str(message_id),str(group)))
 service_log.event('surf-bot','owner manual message group='+str(group)+' message_id='+str(message_id))

def owner_for(db,cfg,group,message_id):
 mid=str(message_id).removeprefix('bot:')
 row=db.execute("SELECT 1 FROM operator_log WHERE target=? AND message_id=? AND kind IN ('group','group-image') AND status='sent' LIMIT 1",(str(group),mid)).fetchone()
 return str(cfg.get('alerts',{}).get('owner_qq','')) if row else None
