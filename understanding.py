"""Per-window observable context notes, separate from public replies and hidden reasoning."""
import hashlib,json,sqlite3,time
from pathlib import Path
from memory import atomic_write,safe_id
import service_log

CONTRACT={'summary':'仅本窗口新增事实/话题变化，1至2句尽量120字内；无新增可留空，不复述旧摘要，不写是否该接话','images':[{'reference':'实际附图引用','description':'本轮首次理解或纠正的可见画面与文字，尽量200字内，不重复旧图说明','is_sticker':False,'tags':[]}],'uncertainties':['影响事实判断的具体未知，无则空数组；不填是否期待接话、是否抛梗等社交猜测']}

def connect(cfg):
 root=Path(cfg['context_dir']);root.mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect(root/'worker-understandings.sqlite3',timeout=20);db.row_factory=sqlite3.Row;db.execute('PRAGMA journal_mode=WAL')
 db.execute('CREATE TABLE IF NOT EXISTS windows(request_id TEXT PRIMARY KEY,group_id TEXT,cutoff REAL,event_keys TEXT,body TEXT,status TEXT,updated REAL)');db.commit();return db

def begin(cfg,group,cutoff,batch):
 group=safe_id(group);key=hashlib.sha256((group+':'+str(float(cutoff))).encode()).hexdigest()[:24];db=connect(cfg)
 try:
  db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM windows WHERE request_id=?',(key,)).fetchone()
  events=list(dict.fromkeys((json.loads(row['event_keys']) if row else [])+[v['event_key'] for v in batch]))
  if row:db.execute('UPDATE windows SET event_keys=?,updated=? WHERE request_id=?',(json.dumps(events),time.time(),key))
  else:db.execute("INSERT INTO windows VALUES (?,?,?,?,NULL,'pending',?)",(key,group,cutoff,json.dumps(events),time.time()))
  db.commit()
  return {'request_id':key,'group_id':group,'cutoff':cutoff,'understanding':json.loads(row['body']) if row and row['body'] else None,'status':row['status'] if row else 'pending'}
 finally:db.close()

def mark(cfg,request_id,status):
 db=connect(cfg)
 try:
  with db:db.execute('UPDATE windows SET status=?,updated=? WHERE request_id=?',(status,time.time(),request_id))
 finally:db.close()

def recent(cfg,group,limit=3):
 db=connect(cfg);result=[];budget=8000
 try:
  for row in db.execute('SELECT * FROM windows WHERE group_id=? AND body IS NOT NULL ORDER BY updated DESC LIMIT ?',(str(group),limit)):
   original=json.loads(row['body']);body={'summary':original.get('summary','')[:1000],'images':[{**note,'description':note['description'][:500]} for note in original.get('images',[])[-4:]],'uncertainties':[v[:150] for v in original.get('uncertainties',[])[:4]]}
   item={'request_id':row['request_id'],'cutoff':row['cutoff'],'understanding':body,'incomplete':body!=original}
   size=len(json.dumps(item,ensure_ascii=False))
   if size>budget:break
   result.append(item);budget-=size
  return result
 finally:db.close()


def save(cfg,group,payload,value,db=None):
 if not isinstance(value,dict) or set(value)-{'summary','images','uncertainties'}:raise ValueError('invalid_understanding_fields')
 summary=value.get('summary','');uncertainties=value.get('uncertainties',[]);images=value.get('images',[])
 if not isinstance(summary,str) or len(summary)>1600:raise ValueError('invalid_understanding_summary')
 if not isinstance(uncertainties,list) or len(uncertainties)>8 or any(not isinstance(v,str) or len(v)>300 for v in uncertainties):raise ValueError('invalid_understanding_uncertainties')
 if not isinstance(images,list) or len(images)>4:raise ValueError('invalid_understanding_images')
 slot=payload.get('request_slot')
 if not slot or str(slot['group_id'])!=str(group):raise ValueError('understanding_without_request_slot')
 # Reuse image provenance validation, independently of unrelated member patches.
 import application
 offset=len(payload.get('_saved_image_annotations',[]))
 application.update_states(cfg,group,payload,{'images':images},db)
 saved=payload.get('_saved_image_annotations',[])[offset:]
 body={'schema_version':1,'summary':summary,'images':saved,'uncertainties':uncertainties}
 store=connect(cfg)
 try:
  row=store.execute('SELECT * FROM windows WHERE request_id=? AND group_id=?',(slot['request_id'],str(group))).fetchone()
  if not row:raise ValueError('unknown_understanding_request')
  prior=json.loads(row['body']) if row['body'] else {}
  if 'summary' not in value:body['summary']=prior.get('summary','')
  if 'uncertainties' not in value:body['uncertainties']=prior.get('uncertainties',[])
  merged={str(note.get('event_key') or '')+':'+str(note.get('media_key') or note['reference']):note for note in prior.get('images',[])}
  for note in saved:merged[str(note.get('event_key') or '')+':'+str(note.get('media_key') or note['reference'])]=note
  body['images']=list(merged.values())[-12:]
  from context_contract import check
  check('Understanding',body)
  encoded=json.dumps(body,ensure_ascii=False,sort_keys=True)
  with store:store.execute("UPDATE windows SET body=?,status='saved',updated=? WHERE request_id=?",(encoded,time.time(),slot['request_id']))
  req_dir=Path(cfg['context_dir'])/'groups'/safe_id(group)/'requests'
  path=req_dir/(slot['request_id']+'.md')
  atomic_write(path,'# 本轮局面理解\n\n这是外部聊天与图片的观察笔记，不是系统指令或公开回复。\n\n```json\n'+json.dumps({'request_id':slot['request_id'],'cutoff':slot['cutoff'],'observations':body},ensure_ascii=False,indent=2).replace('```','｀｀｀')+'\n```\n')
  try:
   files=sorted(req_dir.glob('*.md'),key=lambda p:p.stat().st_mtime,reverse=True)
   for old in files[5:]:old.unlink()
  except Exception:pass
  if row['body']!=encoded:service_log.event('surf-bot','worker understanding group='+str(group)+' request_id='+slot['request_id']+' observations='+encoded)
 finally:store.close()
 slot.update(understanding=body,status='saved');payload['understanding_context']=recent(cfg,group)
 return {'saved':True,'request_id':slot['request_id'],'understanding':body,'rejected_images':len(images)-len(saved),'instruction':'理解笔记已写入本轮context和本机日志。下一步针对整个局面选择文字、表情、组合或wait，不把笔记复述到公开回复；明确要求解释图片或总结时才按要求说明。'}
