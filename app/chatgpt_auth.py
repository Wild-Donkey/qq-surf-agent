"""Official Sign in with ChatGPT for this local runtime; never imports Codex credentials."""
import argparse
from app.protocol import parse_json
import base64
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'runtime' / 'chatgpt'
RESOURCE = 'https://api.openai.com/v1'
AUTH = 'https://auth.openai.com'
TOKEN = AUTH + '/api/accounts/oauth/token'
SCOPE = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def request_json(url, body=None, token=None):
    headers = {'Accept': 'application/json'}
    if body is not None: headers['Content-Type'] = 'application/x-www-form-urlencoded'
    if token: headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=urllib.parse.urlencode(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def active_record():
    active = read(PRIVATE / 'active.json', {})
    key = active.get('registration', '')
    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('尚未登录 ChatGPT，请运行 chatgpt_auth.py login')
    path = PRIVATE / (key + '.json')
    return path, read(path)


def access_token():
    PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (PRIVATE/'refresh.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _access_token_locked()


def _access_token_locked():
    path, record = active_record()
    if 'chatgpt.tokens.use.direct' not in record.get('scopes', []):
        raise ValueError('ChatGPT 订阅使用权限未授予')
    if record['expires_at'] < time.time() + 90:
        if not record.get('refresh_token'): raise ValueError('ChatGPT 登录已过期，请重新登录')
        tokens = request_json(TOKEN, {'grant_type': 'refresh_token', 'client_id': record['client_id'],
                              'refresh_token': record['refresh_token'], 'resource': RESOURCE})
        if not tokens.get('access_token') or not tokens.get('refresh_token'):
            raise ValueError('续期响应不完整，请重新登录')
        scopes = tokens.get('scope', ' '.join(record['scopes'])).split()
        if 'chatgpt.tokens.use.direct' not in scopes: raise ValueError('ChatGPT 订阅权限已失效')
        record.update(tokens, scopes=scopes, expires_at=time.time() + tokens['expires_in'])
        save(path, record)
    return record['access_token']


def available():
    try:
        _, record = active_record()
        return bool(record and 'chatgpt.tokens.use.direct' in record.get('scopes', []))
    except ValueError:
        return False


def verify_identity(tokens, client_id, nonce):
    # Isolated optional dependency, installed into the ignored runtime directory.
    sys.path.insert(0, str(ROOT / 'runtime' / 'deps'))
    import jwt
    discovery = request_json(AUTH + '/.well-known/openid-configuration')
    if discovery['issuer'] != AUTH: raise ValueError('OpenAI issuer 不匹配')
    if urllib.parse.urlsplit(discovery['jwks_uri']).hostname != 'auth.openai.com':
        raise ValueError('OpenAI JWKS 地址不匹配')
    jwks = jwt.PyJWKClient(discovery['jwks_uri'], timeout=30)
    key = jwks.get_signing_key_from_jwt(tokens['id_token'])
    claims = jwt.decode(tokens['id_token'], key.key, algorithms=['RS256'], audience=client_id,
                        issuer=AUTH, leeway=5, options={'require': ['sub', 'exp', 'iat', 'nonce']})
    if not isinstance(claims['sub'], str) or not claims['sub'] or not hmac.compare_digest(claims['nonce'], nonce):
        raise ValueError('ChatGPT 身份验证失败')
    return claims


def complete(params, pending, existing=None):
    if time.time() > pending['expires_at'] or not hmac.compare_digest(params.get('state', ''), pending['state']):
        raise ValueError('登录状态不匹配或已过期')
    if params.get('error'): raise ValueError('登录未获授权')
    cid = params.get('client_id') or (existing or {}).get('client_id')
    if not cid or cid == 'dynamic_agent_client': raise ValueError('未返回应用注册 ID')
    if existing and cid != existing['client_id']: raise ValueError('应用注册 ID 不匹配')
    if not params.get('code'): raise ValueError('未返回授权码')
    tokens = request_json(TOKEN, {'grant_type': 'authorization_code', 'client_id': cid,
                          'code': params['code'], 'code_verifier': pending['verifier'],
                          'redirect_uri': pending['redirect_uri'], 'resource': RESOURCE})
    claims = verify_identity(tokens, cid, pending['nonce'])
    if existing and existing['subject'] != claims['sub']: raise ValueError('登录账号与选定账号不匹配')
    scopes = tokens.get('scope', '').split()
    if 'chatgpt.tokens.use.direct' not in scopes: raise ValueError('未授予 ChatGPT 订阅使用权限')
    if not tokens.get('access_token'): raise ValueError('缺少访问凭据')
    record = dict(tokens, issuer=AUTH, subject=claims['sub'], email=claims.get('email', ''),
                  client_id=cid, ext_agent_host_id=pending['host_id'], scopes=scopes,
                  expires_at=time.time() + tokens['expires_in'])
    key = hashlib.sha256((cid + ':' + claims['sub']).encode()).hexdigest()
    save(PRIVATE / (key + '.json'), record)
    save(PRIVATE / 'active.json', {'registration': key})


def login(port=8768, new=False):
    host = read(PRIVATE / 'host.json') or {'id': 'urn:uuid:' + __import__('uuid').uuid4().__str__()}
    save(PRIVATE / 'host.json', host)
    existing = None
    if not new:
        try: _, existing = active_record()
        except ValueError: pass
    pending = {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass  # Callback contains credentials; never log its URL.
        def page(self, text, code=200):
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(text.encode())
        def do_GET(self):
            route = urllib.parse.urlsplit(self.path)
            if route.path == '/':
                self.page('<h2>QQ Surf Agent</h2><p>使用 ChatGPT 订阅额度生成群聊回复，与其他订阅使用共享限额。</p><p><a href="/login">Continue with ChatGPT</a></p>')
            elif route.path == '/login':
                if self.headers.get('Sec-Fetch-Site') == 'cross-site': return self.page('请从本机登录页开始', 403)
                pending.clear()
                pending.update(state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32), verifier=secrets.token_urlsafe(48),
                               expires_at=time.time()+600, host_id=host['id'], redirect_uri=f'http://127.0.0.1:{port}/auth/callback')
                challenge=base64.urlsafe_b64encode(hashlib.sha256(pending['verifier'].encode()).digest()).decode().rstrip('=')
                params = {'client_id': existing['client_id'] if existing else 'dynamic_agent_client',
                          'ext_agent_host_id': host['id'], 'response_type': 'code', 'redirect_uri': pending['redirect_uri'],
                          'scope': SCOPE, 'resource': RESOURCE, 'state': pending['state'], 'nonce': pending['nonce'],
                          'code_challenge_method': 'S256', 'code_challenge': challenge}
                if not existing: params['agent_name_hint'] = 'QQ Surf Agent'
                elif existing.get('id_token'): params['id_token_hint'] = existing['id_token']
                self.send_response(302); self.send_header('Location', AUTH+'/api/accounts/authorize?'+urllib.parse.urlencode(params)); self.end_headers()
            elif route.path == '/auth/callback':
                if not pending: return self.page('登录已结束，请重新开始', 400)
                attempt = dict(pending)
                params = {k:v[0] for k,v in urllib.parse.parse_qs(route.query).items() if len(v)==1}
                # Unrelated callbacks cannot consume the legitimate pending transaction.
                if not hmac.compare_digest(params.get('state',''),attempt['state']): return self.page('登录状态不匹配',400)
                pending.clear()
                try:
                    complete(params, attempt, existing)
                    self.page('<h2>ChatGPT 已连接</h2><p>可以回到 Codex。接下来会验证模型并启用服务。</p>')
                    print('ChatGPT 登录与订阅权限验证成功。', flush=True)
                except Exception as exc:
                    self.page('登录失败。请返回本机首页重试；未更改现有连接。',400)
                    print('ChatGPT 登录失败：'+type(exc).__name__,flush=True)
            else: self.page('Not found',404)
    server = HTTPServer(('127.0.0.1', port), Handler)
    print(f'登录页已就绪：http://127.0.0.1:{port}',flush=True)
    server.serve_forever()


def infer(model, instructions, payload, image_inputs=None,text_format=None):
    content=json.dumps(payload,ensure_ascii=False)
    if image_inputs:content=[{'type':'input_text','text':content}]+image_inputs
    body={'model':model, 'instructions':instructions,
          'input':[{'role':'user','content':content}], 'store':False,'stream':True}
    if text_format:body['text']={'format':text_format}
    req=urllib.request.Request(RESOURCE+'/responses',data=json.dumps(body).encode(),
        headers={'Authorization':'Bearer '+access_token(),'Content-Type':'application/json','Accept':'text/event-stream'})
    completed=None; event=[];messages={}
    with urllib.request.urlopen(req,timeout=90) as response:
        for raw in response:
            line=raw.decode('utf-8').rstrip('\r\n')
            if line.startswith('data:'): event.append(line[5:].lstrip())
            elif not line and event:
                data='\n'.join(event);event=[]
                if data=='[DONE]':continue
                item=parse_json(data,'sse_event')
                if item.get('type') in ('error','response.failed','response.incomplete'):
                    raise ValueError('ChatGPT 流式响应未完成')
                if item.get('type')=='response.output_item.done':
                    output=item.get('item',{})
                    if output.get('type')=='message' and output.get('phase') in (None,'final_answer'):
                        messages[item.get('output_index',0)]=output
                if item.get('type')=='response.completed': completed=item['response']
    if not completed or completed.get('status')!='completed':raise ValueError('ChatGPT 连接提前结束')
    output=list(messages[k] for k in sorted(messages)) if messages else completed.get('output',[])
    text=''.join(c.get('text','') for item in output if item.get('type')=='message' and item.get('phase') in (None,'final_answer')
                 for c in item.get('content',[]) if c.get('type')=='output_text')
    if not text:raise ValueError('ChatGPT 未返回最终正文')
    usage=completed.get('usage',{})
    return text,{'prompt_tokens':usage.get('input_tokens',0),'completion_tokens':usage.get('output_tokens',0),'total_tokens':usage.get('total_tokens',0)}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['login','models','status','activate'])
    parser.add_argument('--new',action='store_true')
    parser.add_argument('--model')
    args=parser.parse_args()
    if args.command=='login':login(new=args.new)
    elif args.command in ('models','activate'):
        catalog=request_json(RESOURCE+'/models',token=access_token())
        models=[{'slug':m['slug'],'display_name':m['display_name']} for m in catalog['models'] if m.get('visibility')=='list']
        if args.command=='models':print(json.dumps(models,ensure_ascii=False,indent=2))
        else:
            if args.model not in {m['slug'] for m in models}:raise SystemExit('请用 --model 指定当前账号模型目录中的模型')
            raw,usage=infer(args.model,'仅输出 JSON：{"call":{"name":"wait","arguments":{"reason":"连接测试"}}}',{'test':True})
            from app import social
            social.validate_decision(raw)
            config=read(ROOT/'llm.local.json',{})
            save(PRIVATE/'previous-llm.json',config)
            save(ROOT/'llm.local.json',{'enabled':True,'provider':'chatgpt','model':args.model,
                                      'max_requests_per_day':config.get('max_requests_per_day',60)})
            workflow=read(ROOT/'workflow.json',{})
            if workflow.get('database'):
                import sqlite3
                dbpath=Path(workflow['database'])
                if not dbpath.is_absolute():dbpath=ROOT/dbpath
                with sqlite3.connect(dbpath) as db:
                    db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('model_retry_after','0'))
                    db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('model_state','ready'))
            print('ChatGPT 连接测试成功，已启用模型：'+args.model)
    else:print('ChatGPT 已授权' if available() else 'ChatGPT 尚未授权')
