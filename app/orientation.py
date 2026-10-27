"""Read a same-group transcript before a new session is allowed to participate."""
import json,sqlite3,time
from pathlib import Path
from app.memory import atomic_write,safe_id

def prepare(cfg,group,cutoff,fallback):
 root=Path(cfg['context_dir']);settings=cfg.get('interaction',{})
 since=cutoff-float(settings.get('orientation_lookback_seconds',7200))
 limit=min(1000,max(1,int(settings.get('orientation_messages',300))))
 budget=max(2000,min(60000,int(settings.get('orientation_char_budget',24000))))
 path=root/'archive.sqlite3';rows=[];total=0;quotes={}
 if path.exists():
  db=sqlite3.connect(path);db.row_factory=sqlite3.Row
  try:
   total=db.execute('SELECT count(*) FROM messages WHERE group_id=? AND created>=? AND created<=?',(str(group),since,cutoff)).fetchone()[0]
   rows=[dict(r) for r in db.execute('SELECT message_id,user_id,nickname,text,reply_to,mentions,created FROM messages WHERE group_id=? AND created>=? AND created<=? ORDER BY created DESC,id DESC LIMIT ?',(str(group),since,cutoff,limit))]
   mids=list({str(r['reply_to']) for r in rows if r['reply_to']})
   if mids:
    placeholders=','.join('?' for _ in mids)
    found={str(r['message_id']):dict(r) for r in db.execute('SELECT message_id,user_id,nickname,text,created FROM messages WHERE group_id=? AND message_id IN ('+placeholders+')',(str(group),*mids))}
    for mid in mids:
     saved=found.get(mid)
     if saved:
      quotes[mid]={'message_id':mid,'event_key':str(group)+':'+mid,'speaker':saved['user_id'],'nickname':saved['nickname'],'time':saved['created'],'text':saved['text'][:1200],'available':True}
     else:
      quotes[mid]={'message_id':mid,'event_key':str(group)+':'+mid,'speaker':'unknown','nickname':'未知作者','time':None,'text':'[引用原文未缓存，不猜测]','available':False}
   if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_notes'").fetchone():
    for row in rows:row['visual_notes']=[r[0] for r in db.execute('SELECT description FROM visual_notes WHERE group_id=? AND message_id=? LIMIT 4',(str(group),row['message_id']))]
  finally:db.close()
 messages=[];used=0
 if rows:
  for row in rows:
   text=row['text'][:3000]
   if used+len(text)>budget:break
   used+=len(text)
   item={'event_key':str(group)+':'+str(row['message_id']),'speaker':row['user_id'],'nickname':row['nickname'],'text':text,'time':row['created'],'visual_notes':row.get('visual_notes',[]),'mentions':json.loads(row['mentions'] or '[]')}
   if row['reply_to']:item['reply_to']=quotes.get(str(row['reply_to']),{'message_id':str(row['reply_to']),'event_key':str(group)+':'+str(row['reply_to']),'speaker':'unknown','nickname':'未知作者','time':None,'text':'[引用原文未缓存，不猜测]','available':False})
   messages.append(item)
  messages.reverse()
 else:
  messages=[dict(row) for row in fallback];total=len(messages)
 return {'messages':messages,'coverage':{'group_id':str(group),'since':since,'through':cutoff,'available_messages':total,'included_messages':len(messages),'incomplete':len(messages)<total or not path.exists(),'source':'local_archive' if path.exists() else 'working_context'},'instruction':'先按时间补读原话，建立当前话题、指代与成员之间的关系。引用和说话人分开；确认、推测、未知分开；不要望文生义或拿旧bot解说当证据。'}

def save(cfg,group,payload,args):
 if not payload.get('orientation_required'):raise ValueError('orientation_not_requested')
 if set(args)-{'topics','referents','open_interactions'}:raise ValueError('invalid_orientation_fields')
 topics=args.get('topics',[]);refs=args.get('referents',[]);interactions=args.get('open_interactions',[])
 keys={v.get('event_key') for v in payload['orientation_history']['messages'] if v.get('event_key')}
 def clean_evidence(values):
  if not isinstance(values,list):return []
  return [k for k in values if isinstance(k,str) and k in keys][:8]
 def notes(values):
  if not isinstance(values,list) or len(values)>8:raise ValueError('invalid_orientation_notes')
  result=[]
  for note in values:
   if isinstance(note,str):note={'text':note,'evidence_event_keys':[]}
   if not isinstance(note,dict) or set(note)-{'text','evidence_event_keys'} or not isinstance(note.get('text'),str) or len(note['text'])>500:raise ValueError('invalid_orientation_notes')
   evidence=clean_evidence(note.get('evidence_event_keys',[]))
   result.append({'text':note['text'],'evidence_event_keys':evidence})
  return result
 topics=notes(topics);interactions=notes(interactions)
 if not isinstance(refs,list) or len(refs)>12:raise ValueError('invalid_orientation_referents')
 for ref in refs:
  if not isinstance(ref,dict) or set(ref)!={'expression','interpretation','evidence_event_keys','certainty'}:raise ValueError('invalid_orientation_reference')
  if ref['certainty'] not in ('confirmed','tentative','unknown'):raise ValueError('invalid_orientation_certainty')
  if any(not isinstance(ref[k],str) or len(ref[k])>500 for k in ('expression','interpretation')):raise ValueError('invalid_orientation_reference')
  evidence=clean_evidence(ref['evidence_event_keys'])
  ref['evidence_event_keys']=evidence
  if ref['certainty']=='confirmed' and not evidence:ref['certainty']='tentative'
 value={'schema_version':1,'topics':topics,'referents':refs,'open_interactions':interactions,'coverage':payload['orientation_history']['coverage'],'updated':time.time()}
 from app.context_contract import check
 check('Orientation',value)
 atomic_write(Path(cfg['context_dir'])/'groups'/safe_id(group)/'orientation.json',json.dumps(value,ensure_ascii=False,indent=2)+'\n')
 payload.setdefault('group_context',{})['orientation']=value;payload['orientation_required']=False
 payload.pop('orientation_history',None)
 return {'saved':True,'orientation':value,'instruction':'预读完成。现在结合当前窗口决定是否自然参与；不要把背景笔记当回复，不补答过时消息。'}
