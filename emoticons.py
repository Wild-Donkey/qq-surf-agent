"""Native QQ faces and observed, visually described sticker assets."""
import hashlib,json,sqlite3,time,re
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
 db.execute('BEGIN IMMEDIATE')
 columns={r[1] for r in db.execute('PRAGMA table_info(stickers)')}
 for name,definition in [('original_key','TEXT'),('tags',"TEXT DEFAULT '[]'"),('is_sticker','INTEGER DEFAULT 1'),('reviewed','INTEGER DEFAULT 1'),('uses','INTEGER DEFAULT 0'),('last_used','REAL DEFAULT 0'),('animated','INTEGER DEFAULT 0')]:
  if name not in columns:db.execute('ALTER TABLE stickers ADD COLUMN '+name+' '+definition)
 db.commit()
 return db
def learn(root,group,url,description,media_key,original_key=None,tags=None,is_sticker=True):
 if not media_key:return
 asset_path(root,media_key)
 original_key=original_key or media_key;asset_path(root,original_key)
 identity=hashlib.sha256((str(group)+':asset:'+original_key).encode()).hexdigest()[:24]
 db=connect(root)
 with db:
  old=db.execute('SELECT id FROM stickers WHERE group_id=? AND (original_key=? OR (original_key IS NULL AND media_key=?))',(str(group),original_key,media_key)).fetchone()
  if old:identity=old['id']
  db.execute("""INSERT INTO stickers(id,group_id,url,description,media_key,updated,original_key,tags,is_sticker,reviewed)
    VALUES (?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET description=excluded.description,updated=excluded.updated,
    original_key=excluded.original_key,tags=excluded.tags,is_sticker=excluded.is_sticker,reviewed=1""",
    (identity,str(group),url,description[:600],media_key,time.time(),original_key,json.dumps(tags or [],ensure_ascii=False),int(is_sticker)))
 db.close()
 return identity

def asset_path(root,key):
 if not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{64}\.(?:jpg|png|gif|webp)',key):raise ValueError('invalid_media_key')
 base=Path(root).resolve()/'media'
 if base.is_symlink():raise ValueError('invalid_media_directory')
 directory=base.resolve();path=directory/key
 if path.is_symlink() or not path.resolve().is_relative_to(directory) or not path.is_file() or path.stat().st_size>8*1024*1024:raise ValueError('sticker_cache_missing')
 return path

def observe_asset(root,group,meta):
 if not str(group).isdigit() or not meta.get('media_key'):return
 key=meta['media_key'];asset_path(root,key);original_key=meta.get('original_key') or key
 identity=hashlib.sha256((str(group)+':asset:'+original_key).encode()).hexdigest()[:24]
 db=connect(root)
 with db:
  old=db.execute('SELECT id FROM stickers WHERE group_id=? AND (original_key=? OR (original_key IS NULL AND media_key=?))',(str(group),original_key,key)).fetchone()
  if old:identity=old['id']
  else:
   db.execute('INSERT INTO stickers(id,group_id,url,description,media_key,updated,original_key,tags,is_sticker,reviewed,animated) VALUES (?,?,?,?,?,?,?,?,?,0,?)',
    (identity,str(group),'','尚未理解的已收集图片',key,time.time(),meta.get('original_key') or key,'[]',int(meta.get('sticker_hint',False)),int(meta.get('animated_first_frame',False))))
 db.close()
 return identity

def search(root,group,query='',limit=12,include_unreviewed=False):
 db=connect(root)
 rows=db.execute('SELECT * FROM stickers WHERE group_id=? AND (is_sticker=1 OR reviewed=0) ORDER BY updated DESC',(str(group),)).fetchall();db.close()
 terms=set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]',query.lower()))
 ranked=[]
 for row in rows:
  if not include_unreviewed and not row['reviewed']:continue
  try:asset_path(root,row['media_key']);asset_path(root,row['original_key'] or row['media_key'])
  except ValueError:continue
  text=row['description']+' '+' '.join(json.loads(row['tags'] or '[]'))
  score=sum(t in text.lower() for t in terms)
  if terms and not score:continue
  item={k:row[k] for k in ('id','description','reviewed','uses')}
  item['tags']=json.loads(row['tags'] or '[]');item['animated']=bool(row['animated'])
  ranked.append((score,row['updated'],item))
 ranked.sort(key=lambda r:(r[0],r[1]),reverse=True)
 return [r[2] for r in ranked[:limit]]

def catalog(root,group):
 return {'faces':FACES,'stickers':search(root,group,limit=20),'search_api':'search_emoticons','preview_api':'view_emoticon'}

def preview(root,group,identity):
 db=connect(root);row=db.execute('SELECT * FROM stickers WHERE id=? AND group_id=?',(identity,str(group))).fetchone();db.close()
 if not row:raise ValueError('unknown_sticker')
 asset_path(root,row['media_key'])
 return {'reference':'sticker_preview_'+identity,'cache_root':str(root),'group_id':str(group),'cached_asset_key':row['media_key'],'animated_first_frame':bool(row['animated']),'_original_key':row['original_key'] or row['media_key'],'url':'','metadata':{'_sticker_hint':bool(row['is_sticker'])}}

def mark_used(root,group,selection):
 if not selection or selection.get('kind')!='sticker':return
 db=connect(root)
 with db:db.execute('UPDATE stickers SET uses=uses+1,last_used=? WHERE id=? AND group_id=?',(time.time(),str(selection['id']),str(group)))
 db.close()

def segments(root,group,selection):
 if not selection:return []
 if not isinstance(selection,dict):raise ValueError('invalid_emoticon')
 identity=str(selection.get('id',''))
 if selection.get('kind')=='face':
  if identity not in FACES:raise ValueError('unknown_face')
  return [{'type':'face','data':{'id':identity}}]
 if selection.get('kind')!='sticker':raise ValueError('invalid_emoticon_kind')
 db=connect(root);row=db.execute('SELECT media_key,original_key,reviewed,is_sticker FROM stickers WHERE id=? AND group_id=?',(identity,str(group))).fetchone();db.close()
 if not row or not row['reviewed'] or not row['is_sticker']:raise ValueError('unknown_sticker')
 key=row['original_key'] or row['media_key']
 path=asset_path(root,key)
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
   if len(matches)==1:identity=matches[0]
   elif re.fullmatch('[0-9a-f]{24}',identity):
    selection={'kind':'sticker','id':identity}
   else:return None
  if not isinstance(selection,dict):selection={'kind':'face','id':identity}
 elif isinstance(selection,dict):
  identity=selection.get('id')
  kind=selection.get('kind',selection.get('type'))
  if kind is None:kind='face' if str(identity) in FACES else 'sticker'
  selection={'kind':kind,'id':identity}
 else:return None
 if selection.get('kind') not in ('face','sticker') or isinstance(selection.get('id'),bool) or not isinstance(selection.get('id'),(str,int)):return None
 selection={'kind':selection['kind'],'id':str(selection['id'])}
 try:segments(root,group,selection)
 except (ValueError,OSError):return None
 return selection
