"""Native QQ faces and observed, visually described sticker assets."""
import hashlib,json,sqlite3,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
FACES=json.loads((ROOT/'faces.json').read_text())
def describe(parts):
 out=[]
 for part in parts:
  if not isinstance(part,dict):continue
  data=part.get('data',{})
  if not isinstance(data,dict):continue
  if part.get('type')=='face':
   identity=str(data.get('id',''));out.append('[QQ表情:'+FACES.get(identity,'未知')+'#'+identity+']')
  elif part.get('type') in ('image','mface') and data.get('summary'):
   out.append('[表情包说明:'+str(data['summary'])[:120]+']')
 return '\n'.join(out)
def connect(root):
 root=Path(root);root.mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect(root/'emoticons.sqlite3',timeout=20);db.row_factory=sqlite3.Row
 db.execute('CREATE TABLE IF NOT EXISTS stickers(id TEXT PRIMARY KEY,group_id TEXT,url TEXT,description TEXT,media_key TEXT,updated REAL)')
 return db
def learn(root,group,url,description,media_key):
 if not media_key:return
 identity=hashlib.sha256((str(group)+':'+url).encode()).hexdigest()[:24]
 db=connect(root)
 with db:db.execute('INSERT OR REPLACE INTO stickers VALUES (?,?,?,?,?,?)',(identity,str(group),url,description[:600],media_key,time.time()))
 db.close()
def catalog(root,group):
 db=connect(root)
 rows=db.execute('SELECT id,description FROM stickers WHERE group_id=? ORDER BY updated DESC LIMIT 20',(str(group),)).fetchall();db.close()
 return {'faces':FACES,'stickers':[dict(r) for r in rows]}
def segments(root,group,selection):
 if not selection:return []
 if not isinstance(selection,dict):raise ValueError('invalid_emoticon')
 identity=str(selection.get('id',''))
 if selection.get('kind')=='face':
  if identity not in FACES:raise ValueError('unknown_face')
  return [{'type':'face','data':{'id':identity}}]
 if selection.get('kind')!='sticker':raise ValueError('invalid_emoticon_kind')
 db=connect(root);row=db.execute('SELECT media_key FROM stickers WHERE id=? AND group_id=?',(identity,str(group))).fetchone();db.close()
 if not row:raise ValueError('unknown_sticker')
 key=row['media_key']
 if len(key)!=68 or not key.endswith('.jpg') or any(ch not in '0123456789abcdef' for ch in key[:-4]):raise ValueError('invalid_media_key')
 path=Path(root).resolve()/'media'/key
 if not path.is_file():raise ValueError('sticker_cache_missing')
 import base64
 return [{'type':'image','data':{'file':'base64://'+base64.b64encode(path.read_bytes()).decode()}}]

def safe_selection(root,group,selection):
 """Optional decoration must never invalidate an otherwise usable text reply."""
 if selection is None or selection=='':return None
 if isinstance(selection,bool):return None
 if isinstance(selection,(str,int)):
  identity=str(selection)
  if identity not in FACES:
   matches=[key for key,name in FACES.items() if name==identity]
   if len(matches)!=1:return None
   identity=matches[0]
  selection={'kind':'face','id':identity}
 elif isinstance(selection,dict):
  selection={'kind':selection.get('kind',selection.get('type')),'id':selection.get('id')}
 else:return None
 if selection.get('kind') not in ('face','sticker') or isinstance(selection.get('id'),bool) or not isinstance(selection.get('id'),(str,int)):return None
 selection={'kind':selection['kind'],'id':str(selection['id'])}
 try:segments(root,group,selection)
 except (ValueError,OSError):return None
 return selection
