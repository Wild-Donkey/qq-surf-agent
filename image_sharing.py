"""Explicit single-image sharing; no foreign conversation enters the reply context."""
import hashlib,json,time
from pathlib import Path
import emoticons,participants,merged_forward,memory_store

def ensure_schema(db):
 db.execute('CREATE TABLE IF NOT EXISTS image_share_routes(id TEXT PRIMARY KEY,source_group TEXT,target_group TEXT,created REAL)')
 db.commit()

def destinations(db,cfg,source,now=None):
 now=time.time() if now is None else now;ensure_schema(db)
 if not cfg.get('merged_forward',{}).get('enabled',True):return []
 groups={str(g['id']):g for g in cfg['groups'] if g.get('enabled',True) and g.get('allow_cross_group_forward',True)}
 if str(source) not in groups:return []
 result=[]
 for row in db.execute('SELECT group_id,max(created) latest FROM speaker_events WHERE created>? GROUP BY group_id ORDER BY latest DESC',(now-300,)).fetchall():
  target=row['group_id']
  if target==str(source) or target not in groups or not participants.humans_recent(db,target,cfg,now):continue
  active=db.execute('SELECT last_member_message FROM group_activity WHERE group_id=?',(target,)).fetchone()
  pause=db.execute('SELECT until_time FROM operator_pause WHERE group_id=?',(target,)).fetchone()
  if not active or not 0<=now-active[0]<=cfg.get('inactive_group_days',14)*86400 or pause and pause[0]>now:continue
  last=db.execute("SELECT max(d.updated) FROM deliveries d JOIN candidates c ON c.id=d.candidate_id WHERE d.target_group=? AND c.kind!='reply' AND d.status IN ('sent','uncertain')",(target,)).fetchone()[0]
  if last and now-last<cfg.get('merged_forward',{}).get('cooldown_seconds',60):continue
  route=hashlib.sha256(('image-route:'+str(source)+':'+target).encode()).hexdigest()[:24]
  with db:db.execute('INSERT OR REPLACE INTO image_share_routes VALUES (?,?,?,?)',(route,str(source),target,now))
  profile={}
  if memory_store.enabled(cfg['context_dir']):
   store=memory_store.MemoryStore(cfg['context_dir'])
   try:
    portrait=store.load('group',target)['portrait']
    # Routing hints only; no target messages or current conversation are copied.
    profile={'topics':[f['text'][:80] for f in portrait.get('topics',[])[:5]]}
   finally:store.close()
  notes=cfg.get('image_sharing',{}).get('audience_preferences',{}).get(target,[])
  profile['owner_preferences']=[v[:160] for v in notes[:4] if isinstance(v,str)]
  result.append({'route_id':route,'name':groups[target].get('name',target),'audience_profile':profile,'purpose':'仅用于匹配素材去向，不能把本群话在目标群隔空回复'})
  if len(result)>=3:break
 return result

def gallery(root,source):
 db=emoticons.connect(root)
 rows=db.execute('SELECT id,description,media_key,original_key FROM stickers WHERE group_id=? AND reviewed=1 ORDER BY updated DESC LIMIT 8',(str(source),)).fetchall();db.close()
 return [{'image_id':r['id'],'description':r['description'],'format':'single_image'} for r in rows]

def asset(root,source,image_id):
 db=emoticons.connect(root);row=db.execute('SELECT * FROM stickers WHERE id=? AND group_id=? AND reviewed=1',(str(image_id),str(source))).fetchone();db.close()
 if not row:raise ValueError('share_image_not_understood')
 path=emoticons.asset_path(root,row['original_key'] or row['media_key'])
 if merged_forward.blocked(row['description']):raise ValueError('share_image_private')
 return row,path

def approve(db,cfg,source,decision,now):
 ensure_schema(db)
 route_id=decision.get('route_id');visible={r['route_id'] for r in decision.get('_image_destinations',[])}
 if route_id not in visible:raise ValueError('image_route_outside_context')
 route=db.execute('SELECT * FROM image_share_routes WHERE id=?',(route_id,)).fetchone()
 if not route or route['source_group']!=str(source) or now-route['created']>180:raise ValueError('image_route_expired')
 target=route['target_group'];row,path=asset(cfg['context_dir'],source,decision.get('image_id'))
 fp=hashlib.sha256(('image-share:'+target+':'+path.name).encode()).hexdigest()
 data=json.dumps({'image_id':row['id'],'media_key':path.name,'description':row['description'][:600]})
 with db:
  db.execute("INSERT OR IGNORE INTO candidates(fingerprint,source_group,text,images,created,status,kind) VALUES (?,?,?,'[]',?,'approved','image_share')",(fp,str(source),data,now))
  cid=db.execute('SELECT id FROM candidates WHERE fingerprint=?',(fp,)).fetchone()[0]
  db.execute("INSERT OR IGNORE INTO deliveries(candidate_id,target_group,fingerprint,status,created,updated) VALUES (?,?,?,'approved',?,?)",(cid,target,fp,now,now))
 return db.execute('SELECT id FROM deliveries WHERE candidate_id=? AND target_group=?',(cid,target)).fetchone()[0]

def send_parts(db,cfg,source,target,data):
 groups={str(g['id']) for g in cfg['groups'] if g.get('enabled',True) and g.get('allow_cross_group_forward',True)}
 if not cfg.get('merged_forward',{}).get('enabled',True) or str(source) not in groups or str(target) not in groups or str(source)==str(target):raise ValueError('image_route_not_allowed')
 payload=json.loads(data);row,path=asset(cfg['context_dir'],source,payload['image_id'])
 if path.name!=payload['media_key']:raise ValueError('image_asset_changed')
 import base64
 return [{'type':'image','data':{'file':'base64://'+base64.b64encode(path.read_bytes()).decode()}}]
