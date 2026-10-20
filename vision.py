"""Bounded QQ-CDN image decoding for real multimodal model input."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request
import warnings

ROOT=Path(__file__).resolve().parent
MAX_BYTES=8*1024*1024


def allowed(url):
    parsed=urllib.parse.urlsplit(url)
    host=(parsed.hostname or '').lower()
    return parsed.scheme=='https' and parsed.port in (None,443) and not parsed.username and not parsed.password and (
        host=='multimedia.nt.qq.com.cn' or host=='gchat.qpic.cn' or host.endswith('.qpic.cn'))


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
            if image.format not in ('JPEG','PNG','GIF','WEBP') or image.width*image.height>16_000_000:
                raise ValueError('unsupported_or_oversized_image')
            image.seek(0)  # Animated memes: first frame only, reported explicitly to the worker.
            animated=bool(getattr(image,'is_animated',False))
            image=image.convert('RGBA');image.thumbnail((1600,1600))
            background=Image.new('RGB',image.size,'white');background.paste(image,mask=image.getchannel('A'))
            out=io.BytesIO();background.save(out,format='JPEG',quality=88)
            return out.getvalue(),animated


def fetch(url):
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
                detail.update(media_key=key,event_key=item.get('event_key'),group_id=item.get('group_id'),url=item.get('url') or ('qq-message:'+item.get('event_key','')+':'+str(item.get('ordinal',0))))
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
    url=str(data.get('url',''))
    if url.startswith('http://gchat.qpic.cn/'):url='https://'+url[7:]
    if url and allowed(url):return fetch(url)
    path=data.get('path') or data.get('file')
    if path and str(path).startswith('/'):return local_image(path)
    if path and allowed(str(path)):return fetch(str(path))
    file=str(data.get('file') or data.get('file_id') or '')
    if allow_lookup and file and len(file)<=1024 and not any(c in file for c in '/\\\x00:'):
        return from_metadata(bridge_call('get_image',{'file':file}),False)
    raise ValueError('qq_image_metadata_has_no_usable_asset')


def resolve(item):
    first=None
    if item.get('url'):
        try:return fetch(item['url'])
        except Exception as exc:first=exc
    metadata=item.get('metadata',{})
    file=str(metadata.get('file') or metadata.get('file_id') or '')
    if file and len(file)<=1024 and not any(c in file for c in '/\\\x00:'):
        try:return from_metadata(bridge_call('get_image',{'file':file}),False)
        except Exception as exc:first=exc
    key=item.get('event_key','');group,_,mid=key.partition(':')
    if group.isdigit() and mid.isdigit():
        try:
            message=bridge_call('get_msg',{'message_id':int(mid)})
            if str(message.get('message_id',mid))!=mid or message.get('group_id') is not None and str(message['group_id'])!=group:raise ValueError('qq_image_message_mismatch')
            images=[p['data'] for p in message.get('message',[]) if isinstance(p,dict) and p.get('type')=='image']
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
    directory=Path(root)/'media';directory.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(directory/'references.sqlite3',timeout=20)
    db.execute('CREATE TABLE IF NOT EXISTS refs(id TEXT PRIMARY KEY,media_key TEXT,animated INTEGER,created REAL)')
    identity=item.get('event_key','')+':'+str(item.get('ordinal',0)) if item.get('event_key') else str(item.get('url',''))
    identity=hashlib.sha256(identity.encode()).hexdigest()
    try:
        row=db.execute('SELECT media_key,animated FROM refs WHERE id=?',(identity,)).fetchone()
        if row and len(row[0])==68 and row[0].endswith('.jpg') and all(c in '0123456789abcdef' for c in row[0][:-4]):
            path=directory/row[0]
            if path.is_file():return path.read_bytes(),bool(row[1])
        raw,animated=resolve(item);key=hashlib.sha256(raw).hexdigest()+'.jpg';path=directory/key
        if not path.exists():path.write_bytes(raw)
        with db:db.execute('INSERT OR REPLACE INTO refs VALUES (?,?,?,?)',(identity,key,int(animated),time.time()))
        return raw,animated
    finally:db.close()
