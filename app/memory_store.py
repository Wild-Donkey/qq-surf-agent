"""Versioned schema-validated memory. SQLite commits precede JSON/Markdown projections."""
import copy,hashlib,json,re,sqlite3,time,fcntl
from pathlib import Path
from app.memory import atomic_write
SCHEMA=json.loads((Path(__file__).resolve().parents[1]/'schemas/memory.schema.json').read_text())


def validate(value,schema,path='$',root_schema=None):
 root_schema=SCHEMA if root_schema is None else root_schema
 if '$ref' in schema:return validate(value,root_schema['$defs'][schema['$ref'].split('/')[-1]],path,root_schema)
 if 'const' in schema and (value!=schema['const'] or type(value)!=type(schema['const'])):raise ValueError(path+': wrong constant')
 if 'enum' in schema and value not in schema['enum']:raise ValueError(path+': invalid enum')
 if 'oneOf' in schema:
  matches=0
  for option in schema['oneOf']:
   try:validate(value,option,path,root_schema);matches+=1
   except ValueError:pass
  if matches!=1:raise ValueError(path+': expected exactly one schema')
  return
 types=schema.get('type');types=types if isinstance(types,list) else [types] if types else []
 checks={'object':lambda:isinstance(value,dict),'array':lambda:isinstance(value,list),'string':lambda:isinstance(value,str),'integer':lambda:isinstance(value,int) and not isinstance(value,bool),'number':lambda:isinstance(value,(int,float)) and not isinstance(value,bool) and __import__('math').isfinite(value),'boolean':lambda:isinstance(value,bool),'null':lambda:value is None}
 if types and not any(checks[t]() for t in types):raise ValueError(path+': invalid type')
 if isinstance(value,dict):
  missing=set(schema.get('required',[]))-set(value)
  if missing:raise ValueError(path+': missing '+','.join(sorted(missing)))
  props=schema.get('properties',{});patterns=schema.get('patternProperties',{})
  for key,item in value.items():
   if key in props:validate(item,props[key],path+'.'+key,root_schema)
   else:
    matched=[definition for pattern,definition in patterns.items() if re.fullmatch(pattern,key)]
    if not matched and schema.get('additionalProperties') is False:raise ValueError(path+': unknown field '+key)
    for definition in matched:validate(item,definition,path+'.'+key,root_schema)
 if isinstance(value,list):
  if len(value)>schema.get('maxItems',10**9):raise ValueError(path+': too many items')
  for index,item in enumerate(value):validate(item,schema.get('items',{}),path+'['+str(index)+']',root_schema)
 if isinstance(value,str):
  if len(value)>schema.get('maxLength',10**9):raise ValueError(path+': too long')
  if schema.get('pattern') and not re.fullmatch(schema['pattern'],value):raise ValueError(path+': invalid identifier')
 if isinstance(value,(int,float)) and not isinstance(value,bool):
  if 'minimum' in schema and value<schema['minimum'] or 'maximum' in schema and value>schema['maximum']:raise ValueError(path+': out of range')


def blank(definition):
 schema=SCHEMA['$defs'][definition]
 return {key:('' if value.get('type')=='string' else []) for key,value in schema['properties'].items()}
def coverage():return {'message_count':0,'time_start':None,'time_end':None,'complete':False,'note':'尚未建立样本覆盖'}
def person_group():return {'portrait':blank('PersonPortraitInput'),'state':blank('StateInput'),'coverage':coverage()}
def identity(value):
 value=str(value)
 if not value.isdigit():raise ValueError('invalid_memory_identity')
 return value


def input_patch(value,definition):
 if isinstance(value,str):
  if len(value)>(2800 if definition=='StateInput' else 4000):raise ValueError('legacy_summary_too_long')
  return {'summary':value}
 if not isinstance(value,dict):raise ValueError('memory_patch_must_be_object')
 shape=copy.deepcopy(SCHEMA['$defs'][definition]);shape['required']=[];validate(value,shape)
 return value

def hydrate(value,previous,source,now):
 if isinstance(value,list):
  result=[]
  for item in value:
   enriched=copy.deepcopy(item)
   old=next((p for p in previous or [] if p.get('text')==item.get('text') and p.get('relation')==item.get('relation') and p.get('subject_id')==item.get('subject_id') and p.get('object_id')==item.get('object_id')),None)
   enriched.update(first_seen=old['first_seen'] if old else now,last_seen=now,source=source,status='supported' if item.get('evidence') and item['confidence']>=.85 else 'tentative')
   result.append(enriched)
  return result
 return value

def apply_patch(document,patch,definition,source,now):
 normalized=input_patch(patch,definition)
 for key,value in normalized.items():document[key]=hydrate(value,document.get(key),source,now)


def legacy_profile(markdown,kind,group):
 definition='GroupPortraitInput' if kind=='group' else 'PersonPortraitInput';result=blank(definition)
 sections=re.split(r'(?m)^#{1,4}\s+(.+)\n',markdown);intro=sections[0].strip()
 result['summary']=re.sub(r'<!--.*?-->','',intro,flags=re.S)[:800]
 for index in range(1,len(sections),2):
  heading=sections[index];body=sections[index+1].strip() if index+1<len(sections) else ''
  if not body:continue
  if not result['summary'] and any(word in heading for word in ('画像','样本','称呼')):result['summary']=body[:800]
  if kind=='group':field='topics' if any(w in heading for w in ('话题','文化','兴趣')) else 'communication' if any(w in heading for w in ('风格','方式','互动')) else 'rules' if any(w in heading for w in ('群规','约定')) else 'boundaries' if any(w in heading for w in ('边界','意愿','bot')) else 'uncertainties'
  else:field='aliases' if any(w in heading for w in ('称呼','昵称')) else 'style' if any(w in heading for w in ('风格','表达')) else 'interests' if any(w in heading for w in ('话题','兴趣','偏好')) else 'boundaries' if any(w in heading for w in ('边界','意愿')) else 'uncertainties'
  for text in re.split(r'\n(?=[-*] )',body):
   text=text.strip().lstrip('-* ').strip()
   if not text:continue
   evidence=list(dict.fromkeys(re.findall(r'\b'+re.escape(str(group))+r':[^\s、，。；;）)\]`]+',text)))[:30]
   result[field].append({'text':(heading+'：'+text)[:4000],'evidence':evidence,'confidence':.55})
   result[field]=result[field][:60]
 if not result['summary']:result['summary']=re.sub(r'<!--.*?-->','',markdown,flags=re.S)[:800]
 return result


def merge_portrait(target,source):
 if len(source.get('summary',''))>len(target['summary']):target['summary']=source['summary']
 for key,values in source.items():
  if key=='summary':continue
  seen={json.dumps({k:v for k,v in item.items() if k not in ('first_seen','last_seen')},ensure_ascii=False,sort_keys=True) for item in target[key]}
  for item in values:
   signature=json.dumps({k:v for k,v in item.items() if k not in ('first_seen','last_seen')},ensure_ascii=False,sort_keys=True)
   if signature not in seen and len(target[key])<60:target[key].append(item);seen.add(signature)


def retry_conflicts(method):
 def wrapped(*args,**kwargs):
  for attempt in range(4):
   try:return method(*args,**kwargs)
   except ValueError as exc:
    if str(exc)!='memory_revision_conflict' or attempt==3:raise
    time.sleep(.01)
 return wrapped


class MemoryStore:
 def __init__(self,root):
  self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(self.root/'structured_memory.sqlite3',timeout=20);self.db.row_factory=sqlite3.Row
  self.db.execute('PRAGMA journal_mode=WAL');self.db.executescript('CREATE TABLE IF NOT EXISTS documents(kind TEXT,identity TEXT,revision INTEGER,body TEXT,dirty INTEGER DEFAULT 1,PRIMARY KEY(kind,identity));CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY,created REAL,source TEXT,documents TEXT);');self.db.commit()
  self.upgrade_people()
 def upgrade_people(self):
  for row in self.db.execute("SELECT body FROM documents WHERE kind='person'").fetchall():
   doc=json.loads(row[0])
   if 'portrait' in doc:continue
   atomic_write(self.root/'legacy_memory'/'structured'/doc['user_id']/(str(doc['revision'])+'.json'),json.dumps(doc,ensure_ascii=False,indent=2))
   doc.update(portrait=blank('PersonPortraitInput'),state=blank('StateInput'),coverage=coverage())
   for group,scope in doc['groups'].items():
    merge_portrait(doc['portrait'],scope['portrait'])
    if scope['state'].get('summary'):doc['state']['summary']=(doc['state']['summary']+' '+scope['state']['summary']).strip()[:2800]
    doc['coverage']['message_count']+=scope['coverage']['message_count']
   doc['groups']={};doc['coverage']['note']='跨群人物画像合并迁移；旧结构完整保留，尚未重新语义复核'
   self.commit([doc],'person_global_migration')
 def close(self):self.db.close()
 def path(self,kind,ident,suffix='md'):return self.root/('groups' if kind=='group' else 'people')/identity(ident)/(('group.' if kind=='group' else 'people.')+suffix)
 def load(self,kind,ident):
  ident=identity(ident);row=self.db.execute('SELECT body FROM documents WHERE kind=? AND identity=?',(kind,ident)).fetchone()
  if row:return json.loads(row[0])
  now=time.time();base={'schema_version':1,'revision':0,'updated_at':now,'manual_notes':''}
  if kind=='group':return {**base,'kind':'group','group_id':ident,'name':'','portrait':blank('GroupPortraitInput'),'state':blank('StateInput'),'coverage':coverage()}
  if kind=='person':return {**base,'kind':'person','user_id':ident,'account':{'kind':'unknown','confidence':0,'source':'unclassified','reason':'尚未判断','updated_at':now},'portrait':blank('PersonPortraitInput'),'state':blank('StateInput'),'coverage':coverage(),'groups':{}}
  raise ValueError('invalid_memory_kind')
 def commit(self,documents,source):
  now=time.time();copies=[]
  self.db.execute('BEGIN IMMEDIATE')
  try:
   for document in documents:
    doc=copy.deepcopy(document);kind=doc['kind'];ident=identity(doc['group_id'] if kind=='group' else doc['user_id'])
    old=self.db.execute('SELECT revision FROM documents WHERE kind=? AND identity=?',(kind,ident)).fetchone()
    if (old[0] if old else 0)!=doc['revision']:raise ValueError('memory_revision_conflict')
    doc.update(revision=old[0]+1 if old else 1,updated_at=now)
    validate(doc,SCHEMA['$defs']['Group' if kind=='group' else 'Person']);copies.append((kind,ident,doc))
   for kind,ident,doc in copies:self.db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?,1)',(kind,ident,doc['revision'],json.dumps(doc,ensure_ascii=False)))
   self.db.execute('INSERT INTO changes(created,source,documents) VALUES (?,?,?)',(now,source,json.dumps([d for _,_,d in copies],ensure_ascii=False)))
   self.db.commit()
  except Exception:self.db.rollback();raise
  self.materialize()
 def materialize(self,force=False):
  with (self.root/'structured-memory.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   for row in self.db.execute('SELECT * FROM documents'+('' if force else ' WHERE dirty=1')).fetchall():
    doc=json.loads(row['body']);atomic_write(self.path(row['kind'],row['identity'],'json'),json.dumps(doc,ensure_ascii=False,indent=2)+'\n');atomic_write(self.path(row['kind'],row['identity']),render(doc,self.root))
    with self.db:self.db.execute('UPDATE documents SET dirty=0 WHERE kind=? AND identity=? AND revision=?',(row['kind'],row['identity'],row['revision']))
   fcntl.flock(lock,fcntl.LOCK_UN)
 @retry_conflicts
 def observe_member(self,event):
  group=identity(event['group_id']);user=identity(event['user_id']);doc=self.load('person',user);new_group=False;scope=doc
  sender=event.get('sender',{});name=str(sender.get('card') or sender.get('nickname') or user)[:200];aliases=scope['portrait']['aliases']
  if not any(f['text']==name for f in aliases):
   now=time.time();aliases.append({'text':name,'evidence':[group+':'+str(event['message_id'])],'confidence':1,'first_seen':now,'last_seen':now,'source':'bridge','status':'supported'});self.commit([doc],'bridge')
  elif new_group:self.commit([doc],'bridge')
 def verify_evidence(self,group,patch):
  disk_path=self.root/'archive.sqlite3';disk=sqlite3.connect(disk_path) if disk_path.exists() else None
  try:
   for values in patch.values():
    if not isinstance(values,list):continue
    for fact in values:
     if not fact.get('evidence') and fact['confidence']>=.85:raise ValueError('high_confidence_requires_evidence')
     for key in fact['evidence']:
      prefix,mid=key.split(':',1)
      if group is not None and prefix!=str(group):raise ValueError('cross_group_memory_evidence')
      if disk and not disk.execute('SELECT 1 FROM messages WHERE group_id=? AND message_id=?',(prefix,mid)).fetchone():raise ValueError('memory_evidence_not_found')
  finally:
   if disk:disk.close()
 @retry_conflicts
 def state_updates(self,group,group_patch,members,source='worker'):
  group=identity(group);now=time.time();docs=[]
  if group_patch is not None:
   self.verify_evidence(group,input_patch(group_patch,'StateInput'))
   doc=self.load('group',group);apply_patch(doc['state'],group_patch,'StateInput',source,now);docs.append(doc)
  for member in members:
   user,patch=member[:2];local=len(member)>2 and member[2]=='group'
   self.verify_evidence(group if local else None,input_patch(patch,'StateInput'))
   doc=self.load('person',user);scope=doc['groups'].setdefault(group,person_group()) if local else doc
   apply_patch(scope['state'],patch,'StateInput',source,now);docs.append(doc)
  if docs:self.commit(docs,source)
 @retry_conflicts
 def portrait(self,group,value,member=None,sample_coverage=None,source='history_distillation'):
  group=identity(group);now=time.time();definition='PersonPortraitInput' if member else 'GroupPortraitInput'
  patch=legacy_profile(value,'person' if member else 'group',group) if isinstance(value,str) else value
  if not isinstance(value,str):self.verify_evidence(None if member else group,input_patch(patch,definition))
  doc=self.load('person' if member else 'group',member if member else group);scope=doc
  apply_patch(scope['portrait'],patch,definition,'migration' if isinstance(value,str) else source,now)
  if sample_coverage is not None:scope['coverage']=sample_coverage
  self.commit([doc],source)
 @retry_conflicts
 def account(self,user,profile):
  doc=self.load('person',user)
  if all(doc['account'].get(key)==profile.get(key) for key in ('kind','confidence','source','reason')):return
  doc['account']={'kind':profile['kind'],'confidence':profile['confidence'],'source':profile['source'],'reason':profile['reason'],'updated_at':profile['updated']};self.commit([doc],'account_service')


def plain(value):return re.sub(r'\s+',' ',str(value)).replace('<!--','&lt;!--').replace('-->','--&gt;').strip()
def coverage_text(value):return '记录 '+str(value['message_count'])+' 条；取样内读取完成：'+('是' if value['complete'] else '未核验')+'。'+plain(value['note'])+'\n\n时间范围：'+str(value['time_start'])+' — '+str(value['time_end'])

_ARCHIVE_CACHE=None
def _archive(root):
 global _ARCHIVE_CACHE
 if _ARCHIVE_CACHE is None or _ARCHIVE_CACHE[0]!=str(root):
  db=sqlite3.connect(Path(root)/'archive.sqlite3') if (Path(root)/'archive.sqlite3').exists() else None
  _ARCHIVE_CACHE=(str(root),db)
 return _ARCHIVE_CACHE[1]

def quote_evidence(keys,root):
 """Return a human-readable quote of original message text for evidence keys.

 Falls back to the raw event_key only when the message cannot be found.
 """
 if not keys:return '尚无可核验原话'
 db=_archive(root)
 if db is None:return '、'.join(keys)
 quotes=[]
 for key in keys:
  try:
   gid,mid=key.split(':',1)
   row=db.execute('SELECT text FROM messages WHERE group_id=? AND message_id=?',(gid,mid)).fetchone()
  except (ValueError,sqlite3.Error):row=None
  if row and row[0]:
   snippet=plain(row[0])[:60]
   quotes.append('「'+snippet+'」')
  else:
   quotes.append(key)
 return '、'.join(quotes)

def fact_lines(values,root=None):
 confirmed=[];uncertain=[]
 for value in values:
  text=value.get('text') or (value['subject_id']+' → '+value['object_id']+'：'+value['relation'])
  evidence=quote_evidence(value['evidence'],root) if root else ('、'.join(value['evidence']) or '尚无可核验原话')
  line='- '+plain(text)+'\n  - 依据：'+evidence+'；置信度 '+str(value['confidence'])+'；'+value['status']
  (confirmed if value['status']=='supported' and value['confidence']>=.85 and value['evidence'] else uncertain).append(line)
 result=[]
 if confirmed:result.append('已核实：\n'+'\n'.join(confirmed))
 if uncertain:result.append('待核实（不是已知事实）：\n'+'\n'.join(uncertain))
 return '\n\n'.join(result)
def render_fields(portrait,kind,root=None):
 names={'summary':'概况','topics':'长期话题','communication':'交流方式','rules':'明确群规','aliases':'称呼','style':'表达方式','interests':'反复出现的话题与偏好','relationships':'互动关系','boundaries':'意愿与边界','uncertainties':'待确认与冲突'}
 parts=[]
 for key,value in portrait.items():
  body=plain(value) if isinstance(value,str) else fact_lines(value,root)
  if not body:continue
  parts.append('### '+names[key]+'\n\n'+body)
 return '\n\n'.join(parts)
def render_state(state,root=None):
 labels={'summary':'当前概况','current_topics':'当前话题','open_threads':'未解决事项','decisions':'已明确的约定','boundaries':'当前互动边界'}
 parts=[]
 for key,value in state.items():
  body=plain(value) if isinstance(value,str) else fact_lines(value,root)
  if not body:continue
  parts.append('### '+labels[key]+'\n\n'+body)
 return '\n\n'.join(parts)
def render_group_scope(group,scope,root=None):
 """Compact per-group section in people.md: only group-specific relationships + coverage, not a full portrait."""
 rels=scope['portrait'].get('relationships',[])
 state_summary=plain(scope['state'].get('summary',''))
 parts=['### 样本覆盖\n\n'+coverage_text(scope['coverage'])]
 if rels:parts.append('### 本群特殊关系\n\n'+fact_lines(rels,root))
 if state_summary:parts.append('### 本群近期概况\n\n'+state_summary)
 return '\n<!-- group-scope:'+group+':start -->\n## 本群记录 '+group+'\n\n'+'\n\n'.join(parts)+'\n<!-- group-scope:'+group+':end -->\n'
def render(doc,root=None):
 header='# '+('群记忆 '+doc['group_id'] if doc['kind']=='group' else '成员记忆 '+doc['user_id'])+'\n\nSchema v1；修订 '+str(doc['revision'])+'。此 Markdown 由应用生成，结构化数据见同目录 JSON。\n'
 if doc['kind']=='group':
  body='\n<!-- portrait:start -->\n## 群画像\n\n'+render_fields(doc['portrait'],'group',root)+'\n\n### 样本覆盖\n\n'+coverage_text(doc['coverage'])+'\n<!-- portrait:end -->\n\n<!-- conversation-state:start -->\n## 持续对话状态\n\n'+render_state(doc['state'],root)+'\n<!-- conversation-state:end -->\n'
 else:
  body='\n<!-- person-portrait:start -->\n## 人物画像（跨群累积）\n\n'+render_fields(doc['portrait'],'person',root)+'\n<!-- person-portrait:end -->\n\n<!-- person-state:start -->\n## 人物近期状态\n\n'+render_state(doc['state'],root)+'\n<!-- person-state:end -->\n\n<!-- account-kind:start -->\n## 账号类型（应用管理）\n\n'+('类型：'+doc['account']['kind']+'；置信度：'+str(doc['account']['confidence'])+'；来源：'+doc['account']['source']+'\n\n依据：'+plain(doc['account']['reason']))+'\n<!-- account-kind:end -->\n'
  for group,scope in sorted(doc['groups'].items()):body+=render_group_scope(group,scope,root)
 return header+body+'\n## 手工补充\n\n'+doc['manual_notes']+'\n'


def enabled(root):return (Path(root)/'structured_memory.sqlite3').exists()


def migrate(root,groups=(),profiles=()):
    root=Path(root);store=MemoryStore(root);known={str(g['id']):g.get('name','') for g in groups};now=time.time()
    try:
        for kind,folder,filename in [('group','groups','group.md'),('person','people','people.md')]:
            for path in sorted((root/folder).glob('*/'+filename)):
                ident=path.parent.name
                if not ident.isdigit() or store.db.execute('SELECT 1 FROM documents WHERE kind=? AND identity=?',(kind,ident)).fetchone():continue
                text=path.read_text();digest=hashlib.sha256(text.encode()).hexdigest()[:16]
                atomic_write(root/'legacy_memory'/folder/ident/(digest+'.md'),text)
                doc=store.load(kind,ident)
                manual=re.search(r'(?ms)^## 手工补充\s*\n(.*?)(?=^## |^<!--|\Z)',text)
                if manual:doc['manual_notes']=manual[1].strip()[:20000]
                if kind=='group':
                    doc['name']=known.get(ident,'')
                    profile=re.search(r'<!-- portrait:start -->(.*?)<!-- portrait:end -->',text,flags=re.S)
                    source=profile[1] if profile else text
                    apply_patch(doc['portrait'],legacy_profile(source,'group',ident),'GroupPortraitInput','migration',now)
                    current=re.search(r'<!-- conversation-state:start -->(.*?)<!-- conversation-state:end -->',text,flags=re.S)
                    if current:doc['state']['summary']=current[1].strip()[:2800]
                    count=re.search(r'覆盖 (\d+) 条',source)
                    doc['coverage'].update(message_count=int(count[1]) if count else 0,note='从既有画像迁移；旧 Markdown 已完整归档，事实尚待结构化复核')
                else:
                    group_ids=set(re.findall(r'<!-- group-(?:portrait|state):(\d+):start -->',text))
                    for group in group_ids:
                        scope=person_group()
                        profile=re.search(r'<!-- group-portrait:'+group+r':start -->(.*?)<!-- group-portrait:'+group+r':end -->',text,flags=re.S)
                        if profile:apply_patch(scope['portrait'],legacy_profile(profile[1],'person',group),'PersonPortraitInput','migration',now)
                        state=re.search(r'<!-- group-state:'+group+r':start -->(.*?)<!-- group-state:'+group+r':end -->',text,flags=re.S)
                        if state:scope['state']['summary']=state[1].strip()[:2800]
                        count=re.search(r'本成员 (\d+) 条',profile[1] if profile else '')
                        scope['coverage'].update(message_count=int(count[1]) if count else 0,note='既有画像迁移，证据与原始 Markdown 可追溯')
                        merge_portrait(doc['portrait'],scope['portrait'])
                        if scope['state']['summary']:doc['state']['summary']=(doc['state']['summary']+' '+scope['state']['summary']).strip()[:2800]
                        doc['coverage']['message_count']+=scope['coverage']['message_count']
                    doc['coverage']['note']='以账号为中心合并既有跨群观察；原始 Markdown 已完整保留'
                store.commit([doc],'migration')
        for profile in profiles:store.account(str(profile['user_id']),dict(profile))
        store.materialize()
    finally:store.close()
