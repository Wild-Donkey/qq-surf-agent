"""Application-owned, bounded native forwarding bundles; workers choose opaque IDs."""
import hashlib,json,re,time

def ensure_schema(db):
 db.executescript('''CREATE TABLE IF NOT EXISTS forward_bundles(id TEXT PRIMARY KEY,source_group TEXT,message_ids TEXT,created REAL);
 CREATE TABLE IF NOT EXISTS forward_claims(target_group TEXT,source_group TEXT,message_id TEXT,delivery_id INTEGER,PRIMARY KEY(target_group,source_group,message_id));''');db.commit()

def blocked(text):
 return bool(re.search(r'不要(?:外传|转发|搬)|禁止(?:转发|搬运)|别(?:外传|转发|搬)|隐私|私聊|身份证|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?:QQ|微信|电话|手机|住址|地址)\s*[:：号]',text,re.I))

def available(db,cfg,target,now=None):
 now=time.time() if now is None else now;ensure_schema(db)
 settings=cfg.get('merged_forward',{})
 if not settings.get('enabled',True):return []
 allowed=[str(g['id']) for g in cfg['groups'] if g.get('enabled',True) and g.get('allow_cross_group_forward',True) and str(g['id'])!=str(target)]
 if not any(str(g['id'])==str(target) and g.get('enabled',True) and g.get('allow_cross_group_forward',True) for g in cfg['groups']):return []
 result=[]
 for source in allowed:
  rows=db.execute('''SELECT e.*,j.user_id,j.trigger_mentions,j.trigger_reply FROM events e
   LEFT JOIN social_jobs j ON j.event_key=e.group_id||':'||e.message_id AND j.target_group=e.group_id
   WHERE e.group_id=? AND e.created>? AND e.message_id NOT LIKE 'bot:%' ORDER BY e.created DESC LIMIT 20''',(source,now-900)).fetchall()
  if any(blocked(r['text']) for r in rows):continue
  messages=[];used=0
  for row in reversed(rows):
   if not str(row['message_id']).isdigit() or not row['user_id'] or str(row['user_id'])==str(cfg['bot_id']):continue
   if json.loads(row['trigger_mentions'] or '[]') or row['trigger_reply']:continue
   if db.execute('''SELECT 1 FROM forward_claims f JOIN deliveries d ON d.id=f.delivery_id WHERE f.target_group=? AND f.source_group=? AND f.message_id=? AND d.status NOT IN ('superseded','expired','rejected')''',(str(target),source,row['message_id'])).fetchone():continue
   if used+len(row['text'])>3000:break
   used+=len(row['text']);messages.append({'message_id':row['message_id'],'speaker':row['user_id'],'text':row['text'],'images':len(json.loads(row['images'])),'time':row['created']})
  if len(messages)<2:continue
  ids=[r['message_id'] for r in messages]
  bid=hashlib.sha256(json.dumps([source,ids],separators=(',',':')).encode()).hexdigest()[:24]
  with db:db.execute('INSERT OR IGNORE INTO forward_bundles VALUES (?,?,?,?)',(bid,source,json.dumps(ids),now))
  name=next(g.get('name',source) for g in cfg['groups'] if str(g['id'])==source)
  result.append({'bundle_id':bid,'source_name':name,'messages':messages,'instruction':'选择有趣且完整的一段原话，按原顺序选择至少2条message_ids；不要为了转发凑数，不转私人信息或明确拒绝搬运的内容。'})
  if len(result)>=3:break
 return result

def approve(db,cfg,target,decision,now):
 ensure_schema(db)
 bid=decision.get('bundle_id');ids=decision.get('message_ids')
 allowed={b['bundle_id']:b for b in decision.get('_forward_bundles',[])}
 if bid not in allowed:raise ValueError('forward_bundle_outside_context')
 if not isinstance(ids,list) or not 2<=len(ids)<=20 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ValueError('invalid_forward_message_ids')
 bundle=db.execute('SELECT * FROM forward_bundles WHERE id=?',(bid,)).fetchone()
 if not bundle or now-bundle['created']>900:raise ValueError('forward_bundle_expired')
 source=bundle['source_group'];original=json.loads(bundle['message_ids'])
 visible={m['message_id'] for m in allowed[bid]['messages']}
 if any(mid not in visible for mid in ids) or [mid for mid in original if mid in ids]!=ids:raise ValueError('forward_messages_outside_bundle')
 groups={str(g['id']) for g in cfg['groups'] if g.get('enabled',True) and g.get('allow_cross_group_forward',True)}
 if str(target) not in groups or source not in groups or source==str(target):raise ValueError('forward_route_not_allowed')
 for mid in ids:
  row=db.execute('SELECT text,created FROM events WHERE group_id=? AND message_id=?',(source,mid)).fetchone()
  if not row or blocked(row['text']) or now-row['created']>900:raise ValueError('forward_source_invalid')
 fp=hashlib.sha256(json.dumps(['merged_forward',str(target),source,ids],separators=(',',':')).encode()).hexdigest()
 data=json.dumps({'source_group':source,'message_ids':ids})
 db.execute("BEGIN IMMEDIATE")
 with db:
  db.execute("DELETE FROM forward_claims WHERE delivery_id IN (SELECT id FROM deliveries WHERE status IN ('superseded','expired','rejected'))")
  if any(db.execute('SELECT 1 FROM forward_claims WHERE target_group=? AND source_group=? AND message_id=?',(str(target),source,mid)).fetchone() for mid in ids):return
  db.execute("INSERT OR IGNORE INTO candidates(fingerprint,source_group,text,images,created,status,kind) VALUES (?,?,?,?,?,'approved','merged_forward')",(fp,source,data,'[]',now))
  cid=db.execute('SELECT id FROM candidates WHERE fingerprint=?',(fp,)).fetchone()[0]
  db.execute("INSERT OR IGNORE INTO deliveries(candidate_id,target_group,fingerprint,status,created,updated,context_watermark,deadline_reply) VALUES (?,?,?,'approved',?,?,?,?)",(cid,str(target),fp,now,now,decision.get('_context_watermark'),int(bool(decision.get('_deadline_reply')))))
  delivery=db.execute('SELECT id FROM deliveries WHERE candidate_id=? AND target_group=?',(cid,str(target))).fetchone()[0]
  db.executemany('INSERT INTO forward_claims VALUES (?,?,?,?)',[(str(target),source,mid,delivery) for mid in ids])

def send_payload(db,cfg,target,source,data,now=None):
 now=time.time() if now is None else now
 groups={str(g['id']) for g in cfg['groups'] if g.get('enabled',True) and g.get('allow_cross_group_forward',True)}
 if not cfg.get('merged_forward',{}).get('enabled',True) or str(target) not in groups or str(source) not in groups or str(source)==str(target):raise ValueError('forward_route_not_allowed')
 bundle=json.loads(data);ids=bundle.get('message_ids')
 if str(bundle.get('source_group'))!=str(source) or not isinstance(ids,list) or not 2<=len(ids)<=20 or len(set(ids))!=len(ids):raise ValueError('forward_source_invalid')
 if any(blocked(r[0]) for r in db.execute('SELECT text FROM events WHERE group_id=? AND created>?',(str(source),now-900))):raise ValueError('forward_source_private')
 for mid in ids:
  if not isinstance(mid,str) or not mid.isdigit():raise ValueError('forward_source_invalid')
  original=db.execute('SELECT text,created FROM events WHERE group_id=? AND message_id=?',(str(source),mid)).fetchone()
  if not original or blocked(original['text']) or not 0<=now-original['created']<=900:raise ValueError('forward_source_invalid')
 return {'group_id':int(target),'messages':[{'type':'node','data':{'id':mid}} for mid in ids]}
