"""Task-relevant structured recall, never a per-person utterance transcript."""
import hashlib,json,re
from pathlib import Path
import memory_store,identities
from memory import MarkdownMemory,atomic_write,safe_id

CATEGORIES=('facts','boundaries','recent_state','uncertainties')

def retrieve(cfg,group,payload,query=None,alias=None):
 result={k:[] for k in CATEGORIES};result['schema_version']=1;result['manual_notes']=[];result['coverage']={'structured':memory_store.enabled(cfg['context_dir']),'documents':0}
 if not result['coverage']['structured']:return result
 query=query if query is not None else ' '.join(v.get('text','') for v in payload.get('unread_messages',[])+payload.get('recent_target_messages',[])[-8:])
 terms=MarkdownMemory.search_terms(query);terms={t for t in terms if len(t)>1}
 users=list(dict.fromkeys(str(v.get('speaker',v.get('user_id',''))) for v in reversed(payload.get('unread_messages',[])+payload.get('recent_target_messages',[]))))
 users=list(dict.fromkeys([u for u in users if u.isdigit()]+[str(p['id']) for p in payload.get('member_profiles',[]) if str(p['id']).isdigit()]))[:25]
 registry=identities.Registry(cfg['context_dir']);store=memory_store.MemoryStore(cfg['context_dir']);ranked=[]
 try:
  for row in reversed(payload.get('recent_target_messages',[])+payload.get('unread_messages',[])):
   user=str(row.get('speaker',row.get('user_id','')))
   if user.isdigit():registry.alias(user,row.get('nickname'))
  if alias:
   user=registry.resolve(alias)
   if user not in users:raise ValueError('memory_member_outside_context')
   users=[user]
  documents=[('group',str(group))]+[('person',u) for u in users]
  for kind,ident in documents:
   doc=store.load(kind,ident)
   if not doc['revision']:continue
   result['coverage']['documents']+=1
   subject=registry.alias(ident) if kind=='person' else '本群'
   if doc.get('manual_notes','').strip():result['manual_notes'].append({'subject':subject,'text':doc['manual_notes'][:800],'source':'manual'})
   scopes=[('portrait',doc['portrait']),('state',doc['state'])]
   if kind=='person' and str(group) in doc.get('groups',{}):
    local=doc['groups'][str(group)];scopes.extend([('group_portrait',local['portrait']),('group_state',local['state'])])
   for section,fields in scopes:
    for field,values in fields.items():
     if field=='summary' or not isinstance(values,list):continue
     for fact in values:
      text=fact.get('text') or fact.get('relation','')
      if not text:continue
      overlap=len(terms & MarkdownMemory.search_terms(text))
      if field=='aliases' or not overlap and field not in ('boundaries','open_threads'):continue
      supported=fact.get('status')=='supported' and fact.get('confidence',0)>=.85 and bool(fact.get('evidence'))
      category='uncertainties' if field=='uncertainties' or not supported else 'boundaries' if field=='boundaries' else 'recent_state' if section.endswith('state') else 'facts'
      item={'key':hashlib.sha256((kind+ident+text).encode()).hexdigest()[:16],'subject':subject,'field':field,'text':text[:500],'evidence':fact.get('evidence',[])[:8],'confidence':fact.get('confidence',0),'status':'supported' if supported and field!='uncertainties' else 'tentative','source_revision':doc['revision']}
      if fact.get('subject_id'):item['related_subject']=registry.alias(fact['subject_id'])
      if fact.get('object_id'):item['related_object']=registry.alias(fact['object_id'])
      ranked.append((overlap*3+(8 if category=='boundaries' else 0),fact.get('last_seen',0),category,item))
  seen=set();budget=6500
  for _,_,category,item in sorted(ranked,key=lambda v:(v[0],v[1]),reverse=True):
   signature=(item['subject'],re.sub(r'\W','',item['text']))
   size=len(json.dumps(item,ensure_ascii=False))
   if signature in seen or len(result[category])>=8 or size>budget:continue
   seen.add(signature);budget-=size;result[category].append(item)
 finally:store.close();registry.close()
 result['usage']='由应用按当前话题主动检索。事实与推测分开；有必要弄清的未知可自然询问当事人，先查已有记忆/回溯，不对无关琐事盘问，不把未答复问题写成事实。'
 from context_contract import check
 check('RecallBundle',result)
 return result

def project(payload):
 """One deduplicated timeline and one group memory block at the model boundary."""
 payload=dict(payload);payload['group_context']=dict(payload.get('group_context',{}));payload['bot_identity']=dict(payload.get('bot_identity',{}));timeline=[];positions={};unread=set()
 for row in payload.get('unread_messages',[]):
  if row.get('event_key'):unread.add(row['event_key'])
 for source in (payload.get('last_two_hours',{}).get('messages',[]),payload.get('recent_target_messages',[]),payload.get('unread_messages',[])):
  for row in source:
   item=dict(row);key=item.get('event_key') or (str(item.get('message_id')) if item.get('message_id') else None)
   if key:key=key.replace(':bot:',':',1)
   if key and key in positions:timeline[positions[key]].update(item)
   else:
    if key:positions[key]=len(timeline)
    timeline.append(item)
 # Normalize archive IDs to event keys before deduplication in the caller.
 timeline.sort(key=lambda r:r.get('time',r.get('created',0)))
 for item in timeline:
  if item.get('event_key') in unread:item['unread']=True
  if str(item.get('message_id','')).startswith('poke:'):item['event_kind']='poke_notice'
 payload['batch_instruction']='通读conversation.timeline的当前局面；unread标记本窗口新增。结合group_context里的结构化记忆与回溯背景，作一次整体决策，不逐条回复。'
 if 'decision_scope' in payload:payload['decision_scope']={**payload['decision_scope'],'context_fields':['conversation.timeline','conversation.reference_evidence','group_context','pending_requests']}
 payload['context_version']=1
 payload['conversation']={'timeline':timeline,'reference_evidence':payload.pop('reference_evidence',[]),'history_coverage':{k:v for k,v in payload.get('last_two_hours',{}).items() if k!='messages'}}
 for key in ('recent_target_messages','unread_messages','last_two_hours','trigger_event','already_read_window_summary'):payload.pop(key,None)
 group=payload.setdefault('group_context',{})
 group['memory']=payload.pop('recalled_memory',{})
 group['current_state']=payload.pop('previous_group_state','')
 group['older_context']={'windows':payload.pop('relevant_older_windows',[]),'original_records':payload.pop('relevant_archived_messages',[])}
 # The old group Markdown and chronological summaries are no longer duplicated inline.
 memory=payload.pop('memory',{})
 if isinstance(memory,dict) and memory.get('persona'):payload.setdefault('bot_identity',{})['persona']=memory['persona']
 if payload.get('orientation_history'):group['retrace_input']=payload.pop('orientation_history')
 return payload

def compact(root):
 """Lossless exact-fact dedupe; evidence/history stay available, no semantic guessing."""
 import copy,sqlite3,time
 if not memory_store.enabled(root):return {'documents':0,'duplicates':0}
 root=Path(root);maint=root/'maintenance';maint.mkdir(parents=True,exist_ok=True)
 backup=maint/('memory-before-governance-'+str(time.time_ns())+'.sqlite3')
 source=sqlite3.connect(root/'structured_memory.sqlite3');dest=sqlite3.connect(backup)
 try:source.backup(dest)
 finally:source.close();dest.close()
 for old in maint.glob('memory-before-governance-*'):
  if old.resolve()==backup.resolve() or old.resolve()==backup.with_suffix('').resolve():continue
  if old.is_dir():
   import shutil;shutil.rmtree(old,ignore_errors=True)
  else:old.unlink(missing_ok=True)
 store=memory_store.MemoryStore(root);changed=duplicates=0
 try:
  identities_to_compact=store.db.execute('SELECT kind,identity FROM documents').fetchall()
  for row in identities_to_compact:
   for attempt in range(3):
    doc=store.load(row['kind'],row['identity']);before=copy.deepcopy(doc);removed=0
    projection=store.path(row['kind'],row['identity'])
    if projection.exists():
     markdown=projection.read_text();saved=backup.with_suffix('')/'projections'/(row['kind']+'-'+row['identity']+'.md');atomic_write(saved,markdown)
     manual=re.search(r'(?ms)^## 手工补充\s*\n(.*?)(?=^## |\Z)',markdown)
     if manual:doc['manual_notes']=manual[1].strip()
    sections=[doc['portrait'],doc['state']]
    for local in doc.get('groups',{}).values():sections.extend((local['portrait'],local['state']))
    for fields in sections:
     for field,values in fields.items():
      if not isinstance(values,list):continue
      unique={}
      for fact in values:
       key=json.dumps({k:re.sub(r'\s+',' ',v).strip() if isinstance(v,str) else v for k,v in fact.items() if k in ('text','relation','subject_id','object_id','status')},ensure_ascii=False,sort_keys=True)
       if key not in unique:unique[key]=copy.deepcopy(fact);continue
       old=unique[key];winner=copy.deepcopy(max((old,fact),key=lambda f:(f.get('status')=='supported',f.get('confidence',0))))
       winner['evidence']=list(dict.fromkeys(old.get('evidence',[])+fact.get('evidence',[])))[:30]
       winner['first_seen']=min(old.get('first_seen',0),fact.get('first_seen',0));winner['last_seen']=max(old.get('last_seen',0),fact.get('last_seen',0))
       unique[key]=winner;removed+=1
      fields[field]=list(unique.values())
    if doc==before:break
    try:store.commit([doc],'context_governance_exact_dedupe')
    except ValueError as exc:
     if str(exc)=='memory_revision_conflict' and attempt<2:continue
     raise
    changed+=1;duplicates+=removed;break
  store.materialize(force=True)
 finally:store.close()
 return {'documents':changed,'duplicates':duplicates,'backup':str(backup)}
