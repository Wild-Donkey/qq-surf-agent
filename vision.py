"""Bounded QQ-CDN image decoding for real multimodal model input."""
import base64
import time
from contextvars import ContextVar
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request
import warnings

ROOT=Path(__file__).resolve().parent
MAX_BYTES=32*1024*1024
MAX_PIXELS=64_000_000
_CAPTURE=ContextVar("qq_image_original",default=None)


def allowed(url):
    parsed=urllib.parse.urlsplit(url)
    host=(parsed.hostname or '').lower()
    return parsed.scheme=='https' and parsed.port in (None,443) and not parsed.username and not parsed.password and (
        host=='multimedia.nt.qq.com.cn' or host=='gchat.qpic.cn' or host=='gxh.vip.qq.com' or host.endswith('.qpic.cn'))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not allowed(newurl):raise ValueError('untrusted_image_redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def decode_image(raw):
    sys.path.insert(0,str(ROOT/'runtime'/'deps'))
    from PIL import Image
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('JPEG','MPO','PNG','GIF','WEBP') or image.width*image.height>MAX_PIXELS:
                raise ValueError('unsupported_or_oversized_image')
            source_format=image.format
            image.seek(0)  # Animated memes: first frame only, reported explicitly to the worker.
            animated=bool(getattr(image,'is_animated',False))
            if source_format in ('JPEG','MPO'):image.draft('RGB',(1600,1600))
            image.thumbnail((1600,1600))
            image=image.convert('RGBA')
            background=Image.new('RGB',image.size,'white');background.paste(image,mask=image.getchannel('A'))
            out=io.BytesIO();background.save(out,format='JPEG',quality=88)
            capture=_CAPTURE.get()
            if capture is not None:capture.update(raw=raw,extension={'JPEG':'jpg','MPO':'jpg','PNG':'png','GIF':'gif','WEBP':'webp'}[source_format])
            return out.getvalue(),animated


def fetch(url):
    if url.startswith('http://gchat.qpic.cn/'):url='https://'+url[7:]
    if not allowed(url):raise ValueError('untrusted_image_host')
    request=urllib.request.Request(url,headers={'User-Agent':'QQ-Surf-Agent/1.0'})
    with urllib.request.build_opener(SafeRedirect()).open(request,timeout=15) as response:
        raw=response.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('image_too_large')
    return decode_image(raw)


def prepare(payload):
    """Only private in-process _vision entries become image inputs, never arbitrary model text."""
    clean={k:v for k,v in payload.items() if k not in ('_vision','_attached_images')}
    images=[];failed=[];details=[]
    for item in payload.get('_vision',[])[:4]:
        reference=item['reference']
        try:
            raw,animated=cached_resolve(item)
            images.extend([{'type':'input_text','text':'附图 '+reference+('（动图首帧）' if animated else '')},
                           {'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode(raw).decode(),'detail':'high'}])
            detail={'reference':reference,'animated_first_frame':animated}
            if item.get('cache_root'):
                key=hashlib.sha256(raw).hexdigest()+'.jpg'
                path=Path(item['cache_root'])/'media'/key;path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():path.write_bytes(raw)
                detail.update(received_at=item.get('received_at',time.time()),original_key=item.get('_original_key',key),sticker_hint=bool(item.get('metadata',{}).get('_sticker_hint')),media_key=key,event_key=item.get('event_key'),group_id=item.get('group_id'),url=item.get('url') or ('qq-message:'+item.get('event_key','')+':'+str(item.get('ordinal',0))))
            if item.get('cache_root'):
                import emoticons
                detail['sticker_id']=emoticons.observe_asset(item['cache_root'],item.get('group_id',''),detail)
            details.append(detail)
        except Exception as exc:
            code=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            failed.append({'reference':reference,'event_key':item.get('event_key'),'stage':'image_resolve_decode','error':code[:100]})
    clean['vision_status']={'attached':details,'unavailable':failed}
    return clean,images


def bridge_call(action,payload):
    import agent
    return agent.onebot_call(json.loads((ROOT/'config.json').read_text()),action,payload)


def local_image(path):
    candidate=Path(path).expanduser().resolve()
    base=Path.home()/'Library/Containers/com.tencent.qq/Data'
    roots=[base/'Library/Application Support/QQ',base/'Documents/napcat']
    if not any(candidate.is_relative_to(root.resolve()) for root in roots):raise ValueError('qq_cache_path_rejected')
    if not candidate.is_file() or candidate.stat().st_size>MAX_BYTES:raise ValueError('qq_cache_file_unavailable')
    return decode_image(candidate.read_bytes())


def from_metadata(data,allow_lookup=True):
    failures=[]
    url=str(data.get('url',''))
    path=data.get('path') or data.get('file')
    for candidate in dict.fromkeys(str(v) for v in (url,path) if v):
        try:
            if candidate.startswith('http://gchat.qpic.cn/') or allowed(candidate):return fetch(candidate)
            if candidate.startswith('/'):return local_image(candidate)
        except Exception as exc:failures.append(exc)
    file=str(data.get('file') or data.get('file_id') or '')
    if allow_lookup and file and len(file)<=1024 and not any(c in file for c in '/\\\x00:'):
        try:return from_metadata(bridge_call('get_image',{'file':file}),False)
        except Exception as exc:failures.append(exc)
    if failures:raise failures[-1]
    raise ValueError('qq_image_metadata_has_no_usable_asset')


def resolve(item):
    first=None
    if item.get('url'):
        try:return fetch(item['url'])
        except Exception as exc:first=exc
    metadata=item.get('metadata',{})
    if metadata:
        try:return from_metadata(metadata,False)
        except Exception as exc:first=exc
    file=str(metadata.get('file') or metadata.get('file_id') or '')
    if file and len(file)<=1024 and not any(c in file for c in '/\\\x00:'):
        try:return from_metadata(bridge_call('get_image',{'file':file}),False)
        except Exception as exc:first=exc
    key=item.get('event_key','');group,_,mid=key.partition(':')
    if group.isdigit() and mid.isdigit():
        try:
            message=bridge_call('get_msg',{'message_id':int(mid)})
            if str(message.get('message_id',mid))!=mid or message.get('group_id') is not None and str(message['group_id'])!=group:raise ValueError('qq_image_message_mismatch')
            images=[p['data'] for p in message.get('message',[]) if isinstance(p,dict) and p.get('type') in ('image','mface')]
            ordinal=int(item.get('ordinal',0))
            if ordinal>=len(images):raise ValueError('qq_image_segment_missing')
            return from_metadata(images[ordinal])
        except Exception as exc:first=exc
    if first:raise first
    raise ValueError('missing_image_url_and_file')


def cached_resolve(item):
    import sqlite3,time
    root=item.get('cache_root')
    if not root:return resolve(item)
    if item.get('cached_asset_key'):
        import emoticons
        return emoticons.asset_path(root,item['cached_asset_key']).read_bytes(),bool(item.get('animated_first_frame',False))
    directory=Path(root)/'media';directory.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(directory/'references.sqlite3',timeout=20)
    db.execute('CREATE TABLE IF NOT EXISTS refs(id TEXT PRIMARY KEY,media_key TEXT,animated INTEGER,created REAL)')
    db.execute('BEGIN IMMEDIATE')
    columns={r[1] for r in db.execute('PRAGMA table_info(refs)')}
    if 'original_key' not in columns:
        db.execute('ALTER TABLE refs ADD COLUMN original_key TEXT')
    db.commit()
    identity=item.get('event_key','')+':'+str(item.get('ordinal',0)) if item.get('event_key') else str(item.get('url',''))
    identity=hashlib.sha256(identity.encode()).hexdigest()
    try:
        row=db.execute('SELECT media_key,animated,original_key FROM refs WHERE id=?',(identity,)).fetchone()
        if row and len(row[0])==68 and row[0].endswith('.jpg') and all(c in '0123456789abcdef' for c in row[0][:-4]):
            path=directory/row[0]
            if path.is_file():
                item['_original_key']=row[2] or row[0]
                return path.read_bytes(),bool(row[1])
        capture={};token=_CAPTURE.set(capture)
        try:raw,animated=resolve(item)
        finally:_CAPTURE.reset(token)
        key=hashlib.sha256(raw).hexdigest()+'.jpg';path=directory/key
        if not path.exists():path.write_bytes(raw)
        original_key=key
        if capture.get('raw'):
            original_key=hashlib.sha256(capture['raw']).hexdigest()+'.'+capture['extension']
            original=directory/original_key
            if not original.exists():original.write_bytes(capture['raw'])
        item['_original_key']=original_key
        with db:db.execute('INSERT OR REPLACE INTO refs(id,media_key,animated,created,original_key) VALUES (?,?,?,?,?)',(identity,key,int(animated),time.time(),original_key))
        return raw,animated
    finally:db.close()
