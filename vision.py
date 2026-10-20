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
    clean={k:v for k,v in payload.items() if k!='_vision'}
    images=[];failed=[];details=[]
    for item in payload.get('_vision',[])[:4]:
        reference=item['reference']
        try:
            raw,animated=fetch(item['url'])
            images.extend([{'type':'input_text','text':'附图 '+reference+('（动图首帧）' if animated else '')},
                           {'type':'input_image','image_url':'data:image/jpeg;base64,'+base64.b64encode(raw).decode(),'detail':'high'}])
            detail={'reference':reference,'animated_first_frame':animated}
            if item.get('cache_root'):
                key=hashlib.sha256(raw).hexdigest()+'.jpg'
                path=Path(item['cache_root'])/'media'/key;path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():path.write_bytes(raw)
                detail.update(media_key=key,event_key=item.get('event_key'),group_id=item.get('group_id'),url=item['url'])
            details.append(detail)
        except Exception as exc:
            failed.append({'reference':reference,'error':type(exc).__name__})
    clean['vision_status']={'attached':details,'unavailable':failed}
    return clean,images
