"""Global, persistent speaker handles; numeric identities stay inside the application."""
import hashlib,json,re,sqlite3,unicodedata
from pathlib import Path

def short(value):
 result='';width=0
 for c in str(value).strip():
  if unicodedata.category(c).startswith('C'):continue
  size=2 if unicodedata.east_asian_width(c) in ('W','F') else 1
  if width+size>10:break
  result+=c;width+=size
 return result.strip() or '群友'

def clean_card(value):
 """Drop control/format/invisible code points from a group card. Returns '' when nothing visible remains."""
 if value is None:return ''
 return ''.join(c for c in str(value) if unicodedata.category(c)[0]!='C').strip()

def fit_card(value,limit=24):
 """Width-aware trim that never leaves a dangling opening bracket ('蔬菜布丁（')."""
 result='';width=0
 for c in str(value).strip():
  size=2 if unicodedata.east_asian_width(c) in ('W','F') else 1
  if width+size>limit:break
  result+=c;width+=size
 result=result.rstrip()
 for opener,closer in (('（','）'),('(',')'),('【','】'),('[',']'),('{','}'),('《','》'),('〈','〉'),('「','」')):
  if opener in result and closer not in result:result=result.split(opener)[0].rstrip()
 return result.strip() or '群友'

_AT_QQ=re.compile(r'@(\d{5,12})')

def load_group_cards(root,group):
 """Latest in-group card (card preferred over account nickname) for every speaker seen."""
 cards={}
 root=Path(root)
 def collect(path,sql,params=()):
  if not path.exists():return
  db=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
  try:
   for user,name in db.execute(sql,params):
    user=str(user)
    if user not in cards or not clean_card(cards[user]):cards[user]=name
  finally:db.close()
 collect(root/'memory.sqlite3','SELECT user_id,nickname FROM observations WHERE group_id=? ORDER BY created DESC',(str(group),))
 collect(root/'archive.sqlite3','SELECT user_id,nickname FROM messages WHERE group_id=? ORDER BY id DESC LIMIT 20000',(str(group),))
 return cards

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
 bot=str(payload.get('_identity_bot',''))
 group_id=str(payload.get('decision_scope',{}).get('group_id') or payload.get('group_context',{}).get('id') or '')
 cards=load_group_cards(root,group_id) if group_id else {}
 def handle(user,name=None):
  return 'Luna' if str(user)==str(payload.get('_identity_bot','')) and user else registry.alias(user,name)
 chosen={};name_owner={}
 def display(user,inline=None):
  user=str(user)
  if user=='self' or (bot and user==bot):return 'Luna'
  if user in ('unknown','None',''):return '未知作者'
  if user in chosen:return chosen[user]
  base=''
  for candidate in (inline,cards.get(user)):
   cleaned=clean_card(candidate)
   if cleaned and cleaned!=user:base=fit_card(cleaned);break
  if not base:base=registry.alias(user)
  owner=name_owner.get(base)
  if owner is None:name_owner[base]=user
  elif owner!=user:
   suffix=registry.alias(user)
   tail=suffix.rsplit('·',1)[-1] if '·' in suffix else hashlib.sha256(user.encode()).hexdigest()[:4]
   base=fit_card(base,18)+'·'+tail
  chosen[user]=base
  return base
 def render_text_line(value,text):
  name=display(value.get('speaker',value.get('user_id')),value.get('nickname'))
  body=_AT_QQ.sub(lambda m:'@'+display(m.group(1)),str(text))
  return name+'：'+body
 def walk(value,details=False):
  if isinstance(value,list):return [walk(v,details) for v in value]
  if not isinstance(value,dict):return value
  if details:return value
  if value.get('call',{}).get('name') in ('get_member','get_memory_schema'):return value
  result={}
  speaker=value.get('speaker',value.get('user_id'))
  is_message=speaker is not None and 'text' in value
  for key,item in value.items():
   if key in ('_identity_root','_identity_bot','_identity_owner'):continue
   if key in ('member_profiles','member_memories'):continue
   if key=='_vision':result[key]=item;continue
   if key in ('user_id','speaker') and not is_message and isinstance(item,(str,int)):
    result['actor']=handle(item,value.get('nickname'));continue
   if key=='nickname' and not is_message:continue
   if is_message and key in ('speaker','user_id','nickname','text'):
    if key=='text':
     result['line']=render_text_line(value,item)
     result['by']=handle(speaker)
    continue
   if key=='mentions' and isinstance(item,list):result[key]=[handle(v) for v in item];continue
   result[key]=walk(item,details)
  return result
 try:
  from app import context_memory
  for row in reversed(payload.get('recent_target_messages',[])+payload.get('unread_messages',[])):
   user=str(row.get('speaker',row.get('user_id','')))
   if user.isdigit() and user!=str(payload.get('_identity_bot','')):registry.alias(user,row.get('nickname'))
  # Pre-pick display names in timeline order so the earliest speakers keep the plain card.
  for row in payload.get('recent_target_messages',[])+payload.get('unread_messages',[]):
   display(row.get('speaker',row.get('user_id','')),row.get('nickname'))
  normalized=dict(payload)
  group_id=str(payload.get('decision_scope',{}).get('group_id',''))
  history=dict(payload.get('last_two_hours',{}))
  history['messages']=[{**row,'event_key':row.get('event_key') or (group_id+':'+str(row['message_id']) if row.get('message_id') is not None else None)} for row in history.get('messages',[])]
  normalized['last_two_hours']=history
  result=walk(context_memory.project(normalized))
  roles=[]
  owner=str(payload.get('_identity_owner',''));bot=str(payload.get('_identity_bot',''))
  for profile in payload.get('member_profiles',[]):
   user=str(profile['id']);judgment=profile.get('current_judgment',{})
   kind=judgment.get('kind','unknown')
   roles.append({'alias':handle(user),'role':'self' if user==bot else 'owner' if user==owner else 'peer','account_kind':kind,'kind_source':judgment.get('source','unknown')})
  names=['Luna']
  archive=Path(root)/'archive.sqlite3'
  if bot and archive.exists():
   disk=sqlite3.connect(archive)
   try:
    group=str(payload.get('decision_scope',{}).get('group_id',''))
    rows=disk.execute('SELECT raw_json,nickname FROM messages WHERE user_id=? AND group_id=? ORDER BY id DESC LIMIT 5',(bot,group)).fetchall()
    for raw,name in rows:
     if name and name!=bot and name not in names:names.append(name)
     try:account=json.loads(raw).get('sender',{}).get('nickname')
     except (ValueError,TypeError):account=None
     if account and account!=bot and account not in names:names.append(account)
   finally:disk.close()
  self_context={'names':names,'owner_alias':handle(owner) if owner else None,'facts':['你就是当前在本群发言的Luna AI bot，不是聊天记录之外的旁观者。','你的服务由运营者运行。具体部署机器、持续在线时长和其他模型的归属需要证据，不能猜。'],'reference_policy':'结合指代、引用、成员角色和上下文识别隐含互动。讨论bot、模型、你的部署/能力/作息时可以与你有关，无需出现名字；但话题相关不等于明确叫你，也不能把别人的模型认成自己。'}
  result['bot_identity']={**result.get('bot_identity',{}),**self_context}
  result['group_context']={**result.get('group_context',{}),'participants':roles,'portrait':result.pop('group_portrait',{})}
  result['speaker_contract']='conversation.timeline 每行 line 的行首是该成员在本群的名片式称呼（即“谁在说”），by 是他的稳定全局代号，行内 @已还原为人名，mentions 是被@成员的代号，reply_to.line 是本条引用回复的那条原话及其作者。判断“谁在对谁说话”必须同时核对行首发言人、reply_to 作者和 mentions，不要把消息当无主文本。需要QQ号、用户名、群名片或画像时调用get_member(by)；更新成员状态前先get_member获取真实user_id，不要把称呼或代号当user_id。称呼有可靠证据时调用set_member_alias(by,name,evidence_event_key)。不要猜身份或合并同名成员。'
  from app.context_contract import check
  check('WorkerContext',result)
  return result
 finally:registry.close()
