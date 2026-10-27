"""Keep authorship on quotes and retrieve same-group provenance before worker inference."""
from pathlib import Path
import time;from app import archive

def quote(db,root,group,mid):
 group=str(group);mid=str(mid).removeprefix('bot:');saved=None
 if (Path(root)/'archive.sqlite3').exists():
  disk=archive.Archive(root)
  try:saved=disk.get(group,mid)
  finally:disk.close()
 if saved:return {'message_id':mid,'event_key':group+':'+mid,'speaker':saved['user_id'],'nickname':saved['nickname'],'time':saved['created'],'text':saved['text'][:1200],'available':True}
 row=db.execute('''SELECT e.message_id,e.text,e.created,j.user_id FROM events e LEFT JOIN social_jobs j
 ON j.event_key=e.group_id||':'||e.message_id AND j.target_group=e.group_id
 WHERE e.group_id=? AND e.message_id IN (?,?) LIMIT 1''',(group,mid,'bot:'+mid)).fetchone()
 if row:return {'message_id':mid,'event_key':group+':'+mid,'speaker':'self' if row['message_id'].startswith('bot:') else str(row['user_id'] or 'unknown'),'nickname':'自己' if row['message_id'].startswith('bot:') else str(row['user_id'] or 'unknown'),'time':row['created'],'text':row['text'][:1200],'available':True}
 return {'message_id':mid,'event_key':group+':'+mid,'speaker':'unknown','nickname':'未知作者','time':None,'text':'[引用原文未缓存，不猜测]','available':False}

def enrich(cfg,group,payload):
 root=cfg['context_dir'];bot=str(cfg.get('bot_id',''));quotes={}
 for view in payload.get('unread_messages',[])+list(reversed(payload.get('recent_target_messages',[]))):
  ref=view.get('reply_to')
  if ref:
   ref['author_role']='self' if ref.get('speaker') in ('self',bot) else 'member' if ref.get('speaker','').isdigit() else 'unknown'
   if ref['author_role']=='self' and ref.get('nickname')==bot:ref['nickname']='自己'
   if ref.get('available') and len(ref.get('text',''))>=8:quotes.setdefault(ref['message_id'],ref)
 result=[];seen=set();budget=6000
 if (Path(root)/'archive.sqlite3').exists():
  disk=archive.Archive(root)
  try:
   for ref in list(quotes.values())[:3]:
    hits=disk.search(group,ref['text'][:300],before=(ref.get('time') or time.time())+1,limit=16)
    for hit in sorted(hits,key=lambda h:h['speaker']==bot):
     if hit['speaker']==bot or hit['message_id'] in seen:continue
     hit=dict(hit);hit['text']=hit['text'][:800]
     if budget<len(hit['text']):continue
     budget-=len(hit['text']);seen.add(hit['message_id']);hit.update(event_key=str(group)+':'+hit['message_id'],retrieved_for=ref['message_id'],relation='相关原始记录，关联仍须结合原话核实')
     result.append(hit)
     if len(result)>=8:break
    if len(result)>=8:break
  finally:disk.close()
 payload['reference_evidence']=result
 users=set()
 for view in payload.get('unread_messages',[])+payload.get('recent_target_messages',[]):
  users.update(str(user) for user in view.get('mentions',[]) if str(user).isdigit())
  ref=view.get('reply_to',{});speaker=str(ref.get('speaker',''))
  if speaker.isdigit() and speaker!=bot:users.add(speaker)
 for row in result+payload.get('relevant_archived_messages',[]):
  speaker=str(row.get('speaker',''))
  if speaker.isdigit() and speaker!=bot:users.add(speaker)
 return users
