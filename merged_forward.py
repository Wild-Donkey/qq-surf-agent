"""Application-owned, bounded native forwarding bundles; workers choose opaque IDs."""
import hashlib,json,re,time

def ensure_schema(db):
 db.executescript('''CREATE TABLE IF NOT EXISTS forward_bundles(id TEXT PRIMARY KEY,source_group TEXT,message_ids TEXT,created REAL);
 CREATE TABLE IF NOT EXISTS forward_routes(id TEXT PRIMARY KEY,bundle_id TEXT,source_group TEXT,target_group TEXT,created REAL);
 CREATE TABLE IF NOT EXISTS forward_claims(target_group TEXT,source_group TEXT,message_id TEXT,delivery_id INTEGER,PRIMARY KEY(target_group,source_group,message_id));''');db.commit()

def blocked(text):
 return bool(re.search(r'不要(?:外传|转发|搬)|禁止(?:转发|搬运)|别(?:外传|转发|搬)|隐私|私聊|身份证|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?:QQ|微信|电话|手机|住址|地址)\s*[:：号]',text,re.I))

def available(db,cfg,target,now=None,source_only=None,before=None):
 now=time.time() if now is None else now;ensure_schema(db)
 settings=cfg.get('merged_forward',{})
 if not settings.get('enabled',True):return []
 allowed=[str(g['id']) for g in cfg['groups'] if g.get('enabled',True) and g.get('allow_cross_group_forward',True) and str(g['id'])!=str(target)]
 if not any(str(g['id'])==str(target) and g.get('enabled',True) and g.get('allow_cross_group_forward',True) for g in cfg['groups']):return []
 result=[]
 for source in allowed:
  if source_only is not None and source!=str(source_only):continue
  rows=db.execute('''SELECT e.*,j.user_id,j.trigger_mentions,j.trigger_reply FROM events e
   LEFT JOIN social_jobs j ON j.event_key=e.group_id||':'||e.message_id AND j.target_group=e.group_id
   WHERE e.group_id=? AND e.created>? AND e.created<? ORDER BY e.created DESC LIMIT 20''',(source,(now-900) if before is None else before-900,now+1 if before is None else before)).fetchall()
  if any(blocked(r['text']) for r in rows):continue
  messages=[];used=0
  for row in reversed(rows):
   mid=str(row['message_id']).removeprefix('bot:');speaker=str(cfg['bot_id']) if str(row['message_id']).startswith('bot:') else row['user_id']
   if not mid.isdigit() or not speaker or '[合并转发' in row['text'] or '群间搬运｜' in row['text']:continue
   if db.execute('''SELECT 1 FROM forward_claims f JOIN deliveries d ON d.id=f.delivery_id WHERE f.target_group=? AND f.source_group=? AND f.message_id=? AND d.status NOT IN ('superseded','expired','rejected')''',(str(target),source,mid)).fetchone():continue
   if used+len(row['text'])>3000:break
   used+=len(row['text']);messages.append({'message_id':mid,'speaker':speaker,'text':row['text'],'images':len(json.loads(row['images'])),'time':row['created'],'reply_to':row['trigger_reply'] or ''})
  if len(messages)<2:continue
  ids=[r['message_id'] for r in messages]
  bid=hashlib.sha256(json.dumps([source,ids],separators=(',',':')).encode()).hexdigest()[:24]
  with db:db.execute('INSERT OR IGNORE INTO forward_bundles VALUES (?,?,?,?)',(bid,source,json.dumps(ids),now))
  name=next(g.get('name',source) for g in cfg['groups'] if str(g['id'])==source)
  result.append({'bundle_id':bid,'source_name':name,'messages':messages,'instruction':'选择有趣且完整的一段原话，按原顺序选择至少2条message_ids；不要为了转发凑数，不转私人信息或明确拒绝搬运的内容。'})
  if len(result)>=3:break
 return result

def approve(db,cfg,target,decision,now,kind="merged_forward"):
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
 request_key=None
 for key in decision.get('request_ids',[]):
  request=db.execute("SELECT * FROM user_requests WHERE event_key=? AND group_id=? AND status='pending'",(key,source)).fetchone()
  if request and request['kind']=='forward':
   snapshot={str(r['message_id']).removeprefix('bot:') for r in json.loads(request['context_snapshot'] or '[]')}
   if set(ids)<=snapshot:request_key=key;break
 for mid in ids:
  row=db.execute('SELECT text,created FROM events WHERE group_id=? AND message_id IN (?,?)',(source,mid,'bot:'+mid)).fetchone()
  if not row or blocked(row['text']) or not request_key and now-row['created']>900:raise ValueError('forward_source_invalid')
 fp=hashlib.sha256(json.dumps(['merged_forward',str(target),source,ids],separators=(',',':')).encode()).hexdigest()
 data=json.dumps({'source_group':source,'message_ids':ids,'request_key':request_key})
 db.execute("BEGIN IMMEDIATE")
 with db:
  db.execute("DELETE FROM forward_claims WHERE delivery_id IN (SELECT id FROM deliveries WHERE status IN ('superseded','expired','rejected'))")
  claims=[db.execute('SELECT delivery_id FROM forward_claims WHERE target_group=? AND source_group=? AND message_id=?',(str(target),source,mid)).fetchone() for mid in ids]
  if any(claims):
   if all(claims) and len({r[0] for r in claims})==1:return claims[0][0]
   return
  db.execute("INSERT OR IGNORE INTO candidates(fingerprint,source_group,text,images,created,status,kind) VALUES (?,?,?,?,?,'approved',?)",(fp,source,data,'[]',now,kind))
  cid=db.execute('SELECT id FROM candidates WHERE fingerprint=?',(fp,)).fetchone()[0]
  db.execute("INSERT OR IGNORE INTO deliveries(candidate_id,target_group,fingerprint,status,created,updated,context_watermark,deadline_reply) VALUES (?,?,?,'approved',?,?,?,?)",(cid,str(target),fp,now,now,decision.get('_context_watermark'),int(bool(decision.get('_deadline_reply')))))
  delivery=db.execute('SELECT id FROM deliveries WHERE candidate_id=? AND target_group=?',(cid,str(target))).fetchone()[0]
  db.executemany('INSERT INTO forward_claims VALUES (?,?,?,?)',[(str(target),source,mid,delivery) for mid in ids])
 return delivery

def send_payload(db,cfg,target,source,data,now=None):
 now=time.time() if now is None else now
 groups={str(g['id']) for g in cfg['groups'] if g.get('enabled',True) and g.get('allow_cross_group_forward',True)}
 if not cfg.get('merged_forward',{}).get('enabled',True) or str(target) not in groups or str(source) not in groups or str(source)==str(target):raise ValueError('forward_route_not_allowed')
 bundle=json.loads(data);ids=bundle.get('message_ids')
 explicit=False
 if bundle.get('request_key'):
  request=db.execute("SELECT * FROM user_requests WHERE event_key=? AND group_id=? AND status='queued'",(bundle['request_key'],str(source))).fetchone()
  if request:
   receipt=db.execute('SELECT target_group FROM deliveries WHERE id=?',(request['delivery_id'],)).fetchone()
   snapshot={str(r['message_id']).removeprefix('bot:') for r in json.loads(request['context_snapshot'] or '[]')}
   explicit=bool(receipt and receipt[0]==str(target) and isinstance(ids,list) and set(ids)<=snapshot)
 if str(bundle.get('source_group'))!=str(source) or not isinstance(ids,list) or not 2<=len(ids)<=20 or len(set(ids))!=len(ids):raise ValueError('forward_source_invalid')
 if any(blocked(r[0]) for r in db.execute('SELECT text FROM events WHERE group_id=? AND created>?',(str(source),now-900))):raise ValueError('forward_source_private')
 for mid in ids:
  if not isinstance(mid,str) or not mid.isdigit():raise ValueError('forward_source_invalid')
  original=db.execute('SELECT text,created FROM events WHERE group_id=? AND message_id IN (?,?)',(str(source),mid,'bot:'+mid)).fetchone()
  if not original or blocked(original['text']) or not explicit and not 0<=now-original['created']<=900:raise ValueError('forward_source_invalid')
 return {'group_id':int(target),'messages':[{'type':'node','data':{'id':mid}} for mid in ids]}


def outgoing(db,cfg,source,now=None,before=None):
 """Let the source worker publish its own highlights directly to active peers."""
 import participants
 now=time.time() if now is None else now;ensure_schema(db);result=[]
 groups={str(g['id']):g for g in cfg['groups'] if g.get('enabled',True) and g.get('allow_cross_group_forward',True)}
 if str(source) not in groups:return []
 recent=db.execute('SELECT group_id,max(created) latest FROM speaker_events WHERE created>? GROUP BY group_id ORDER BY latest DESC',(now-300,)).fetchall()
 for row in recent:
  target=row['group_id']
  if target==str(source) or target not in groups or not participants.humans_recent(db,target,cfg,now):continue
  active=db.execute('SELECT last_member_message FROM group_activity WHERE group_id=?',(target,)).fetchone()
  if not active or not 0<=now-active[0]<=cfg.get('inactive_group_days',14)*86400:continue
  pause=db.execute('SELECT until_time FROM operator_pause WHERE group_id=?',(target,)).fetchone()
  if pause and pause[0]>now:continue
  last=db.execute("SELECT max(d.updated) FROM deliveries d JOIN candidates c ON c.id=d.candidate_id WHERE d.target_group=? AND c.kind!='reply' AND d.status IN ('sent','uncertain')",(target,)).fetchone()[0]
  if last and now-last<cfg.get('merged_forward',{}).get('cooldown_seconds',60):continue
  bundles=available(db,cfg,target,now,source_only=source,before=before)
  if not bundles:continue
  bundle=bundles[0];route=hashlib.sha256((str(source)+':'+target+':'+bundle['bundle_id']).encode()).hexdigest()[:24]
  with db:db.execute('INSERT OR REPLACE INTO forward_routes VALUES (?,?,?,?,?)',(route,bundle['bundle_id'],str(source),target,now))
  bundle['destination']={'route_id':route,'name':groups[target].get('name',target),'last_activity_seconds_ago':max(0,round(now-row['latest']))}
  bundle['instruction']='本群特别有节目时，挑完整原话片段，merged_forward 带此destination.route_id搬到这个活跃群。不要等别人叫你搬；普通聊天不凑数，不转隐私。'
  result.append(bundle)
  if len(result)>=2:break
 return result

def approve_outgoing(db,cfg,source,decision,now):
 import participants
 ensure_schema(db)
 visible=[b for b in decision.get('_outgoing_moments',[]) if b['destination']['route_id']==decision.get('route_id') and b['bundle_id']==decision.get('bundle_id')]
 if not visible:raise ValueError('forward_route_outside_context')
 route=db.execute('SELECT * FROM forward_routes WHERE id=?',(decision['route_id'],)).fetchone()
 if not route or route['source_group']!=str(source) or route['bundle_id']!=decision.get('bundle_id') or now-route['created']>180:raise ValueError('forward_route_expired')
 target=route['target_group']
 if not participants.humans_recent(db,target,cfg,now):return
 patch=dict(decision,_forward_bundles=visible,_context_watermark=None,_deadline_reply=False)
 return approve(db,cfg,target,patch,now,kind='moment_forward')
