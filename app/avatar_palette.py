"""Background QQ-avatar color sampling. Only fixed QQ CDN URLs; no UI blocking."""
import concurrent.futures,hashlib,io,json,sqlite3,threading,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class AvatarPalette:
 def __init__(self,root=None):
  self.path=Path(root or ROOT/'context')/'ui-colors.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
  with sqlite3.connect(self.path) as d:
   d.execute('CREATE TABLE IF NOT EXISTS colors(kind TEXT,ident TEXT,rgb TEXT,updated REAL,PRIMARY KEY(kind,ident))')
   self.cache={(k,i):(json.loads(rgb) if rgb else None,t) for k,i,rgb,t in d.execute('SELECT * FROM colors')}
  self.lock=threading.Lock();self.busy=set();self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=2)
 def color(self,kind,ident):
  ident=str(ident);key=(kind,ident)
  with self.lock:
   rgb,stamp=self.cache.get(key,(None,0))
   if ident.isdigit() and kind in ('group','user') and time.time()-stamp>(7*86400 if rgb else 3600) and key not in self.busy and len(self.busy)<4:
    self.busy.add(key);self.pool.submit(self.fetch,kind,ident)
  if rgb:return tuple(rgb)
  digest=hashlib.sha256((kind+':'+ident).encode()).digest();return tuple(60+v%180 for v in digest[:3])
 def fetch(self,kind,ident):
  rgb=None
  try:
   host='https://p.qlogo.cn/gh/'+ident+'/'+ident+'/100' if kind=='group' else 'https://q.qlogo.cn/headimg_dl?dst_uin='+ident+'&spec=100&img_type=jpg'
   with urllib.request.urlopen(urllib.request.Request(host,headers={'User-Agent':'qq-surf-console'}),timeout=6) as r:raw=r.read(2_000_001)
   if len(raw)>2_000_000:raise ValueError('avatar_too_large')
   from PIL import Image
   with Image.open(io.BytesIO(raw)) as image:
    if image.width*image.height>4_000_000:raise ValueError('avatar_too_large')
    image=image.convert('RGB');image.thumbnail((48,48));small=image.quantize(colors=8);palette=small.getpalette()
    options=[]
    for count,index in small.getcolors() or []:
     color=tuple(palette[index*3:index*3+3]);saturation=max(color)-min(color)
     options.append((count*(1+saturation/128),color))
    if options:rgb=max(options)[1]
  except Exception:pass
  stamp=time.time()
  with self.lock:self.cache[(kind,ident)]=(rgb,stamp);self.busy.discard((kind,ident))
  try:
   with sqlite3.connect(self.path) as d:d.execute('INSERT OR REPLACE INTO colors VALUES (?,?,?,?)',(kind,ident,json.dumps(rgb) if rgb else None,stamp))
  except sqlite3.Error:pass
 def close(self):self.pool.shutdown(wait=False,cancel_futures=True)
