"""Global, persistent speaker handles; numeric identities stay inside the application."""
import hashlib,json,sqlite3,unicodedata
from pathlib import Path

def short(value):
 result='';width=0
 for c in str(value).strip():
  if unicodedata.category(c).startswith('C'):continue
  size=2 if unicodedata.east_asian_width(c) in ('W','F') else 1
  if width+size>10:break
  result+=c;width+=size
 return result.strip() or '群友'

class Registry:
 def __init__(self,root):
  root=Path(root);root.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(root/'identities.sqlite3');self.db.execute('CREATE TABLE IF NOT EXISTS handles(user_id TEXT PRIMARY KEY,alias TEXT UNIQUE NOT NULL,source TEXT NOT NULL)')
 def close(self):self.db.close()
 def alias(self,user,name=None):
  user=str(user)
  if user=='self':return 'Luna'
  if user in ('unknown','None',''):return '未知作者'
  row=self.db.execute('SELECT alias FROM handles WHERE user_id=?',(user,)).fetchone()
  if row:return row[0]
  # Prefer a stored account nickname over the group card carried by the message.
  archive=Path(self.db.execute('PRAGMA database_list').fetchone()[2]).parent/'archive.sqlite3'
  if archive.exists():
   disk=sqlite3.connect(archive)
   try:
    row=disk.execute('SELECT raw_json,nickname FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 1',(user,)).fetchone()
    if row:
     try:name=json.loads(row[0]).get('sender',{}).get('nickname') or row[1]
     except (ValueError,TypeError):name=row[1]
   finally:disk.close()
  return self.set(user,name if name and str(name)!=user else '群友','initial')
 def set(self,user,name,source):
  base=short(name);alias=base
  owner=self.db.execute('SELECT user_id FROM handles WHERE alias=?',(alias,)).fetchone()
  if alias=='Luna' or owner and owner[0]!=str(user):
   for attempt in range(100):
    suffix=hashlib.sha256((str(user)+':'+str(attempt)).encode()).hexdigest()[:4]
    alias=short(base)[:2]+'·'+suffix
    owner=self.db.execute('SELECT user_id FROM handles WHERE alias=?',(alias,)).fetchone()
    if not owner or owner[0]==str(user):break
   else:raise ValueError('alias_collision')
  with self.db:self.db.execute('INSERT INTO handles VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET alias=excluded.alias,source=excluded.source',(str(user),alias,source))
  return alias
 def resolve(self,value):
  row=self.db.execute('SELECT user_id FROM handles WHERE alias=?',(str(value),)).fetchone()
  return row[0] if row else str(value)

def worker_payload(payload):
 root=payload.get('_identity_root')
 if not root:return payload
 registry=Registry(root)
 def handle(user,name=None):
  return 'Luna' if str(user)==str(payload.get('_identity_bot','')) and user else registry.alias(user,name)
 def walk(value,details=False):
  if isinstance(value,list):return [walk(v,details) for v in value]
  if not isinstance(value,dict):return value
  if details:return value
  if value.get('call',{}).get('name') in ('get_member','get_memory_schema'):return value
  result={}
  speaker=value.get('speaker',value.get('user_id'))
  is_message=speaker is not None and 'text' in value
  alias=handle(speaker,value.get('nickname')) if is_message else None
  for key,item in value.items():
   if key in ('_identity_root','_identity_bot'):continue
   if key in ('member_profiles','member_memories'):continue
   if key=='_vision':result[key]=item;continue
   if key in ('user_id','speaker') and not is_message and isinstance(item,(str,int)):
    result['actor']=handle(item,value.get('nickname'));continue
   if key=='nickname' and not is_message:continue
   if is_message and key in ('speaker','user_id','nickname','text'):
    if key=='text':result['line']=alias+'：'+str(item)
    continue
   if key=='mentions' and isinstance(item,list):result[key]=[handle(v) for v in item];continue
   result[key]=walk(item,details)
  return result
 try:
  result=walk(payload)
  result['speaker_contract']='聊天行只有全局代号：内容，代号不是群名片。需要QQ号、用户名、群名片或画像时调用get_member(alias)。更新成员状态前先查询get_member获取真实user_id；不要把代号当user_id。称呼有可靠证据时调用set_member_alias(alias,name,evidence_event_key)。不要猜身份或合并同名成员。'
  return result
 finally:registry.close()
