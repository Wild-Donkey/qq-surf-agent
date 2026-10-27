#!/usr/bin/env python3
"""Singleton QQ/NapCat wrapper. Closing the console leaves this process alive."""
import fcntl,json,os,signal,subprocess,sys,time,re
from pathlib import Path
from app import service_log
ROOT=Path(__file__).resolve().parents[1]

# QQ stdout 解析：二维码块、登录状态、解码URL
_TS=re.compile(r'^\d{2}-\d{2} \d{2}:\d{2}:\d{2} ')
_QR_CHARS=set('▄█▀▐▌▏▎▍▌▋▊▉█')

def _body(line):
    m=_TS.match(line)
    return line[m.end():] if m else line

def _write_state(state):
    """把登录状态原子写入 runtime/qq-state.json。"""
    path=ROOT/'runtime'/'qq-state.json'
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state,ensure_ascii=False))
    tmp.replace(path)

def run(command):
 work=ROOT/'runtime';work.mkdir(exist_ok=True)
 lock=(work/'qq-helper.lock').open('w')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('QQ Helper wrapper already running')
 pid=work/'qq-helper-service.pid';pid.write_text(str(os.getpid()));child=None
 state={'online':False,'qrcode':None,'qrcode_url':None,'updated_at':None,'last_event':None}
 _write_state(state)
 def stop(*_):
  if child and child.poll() is None:child.terminate()
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 # 二维码块收集缓冲
 qr_buf=[]
 def flush_qr():
  if qr_buf:
   block='\n'.join(qr_buf)
   state['qrcode']=block
   state['updated_at']=time.time()
   _write_state(state)
   qr_buf.clear()
 try:
  service_log.event('qq-helper','Starting QQ Helper')
  child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,cwd=ROOT)
  (work/'qq-helper-child.pid').write_text(str(child.pid))
  while True:
   raw=child.stdout.readline(65536)
   if not raw:break
   line=raw.decode('utf-8',errors='replace').rstrip()
   service_log.event('qq-helper',line)
   body=_body(line)
   state['last_event']=body[:200]
   # 收集二维码行（连续含 ▄/█ 的行）
   if any(c in _QR_CHARS for c in body):
    qr_buf.append(body)
   else:
    if qr_buf and body.strip()=='':
     # 空行可能是二维码块结束
     pass
    elif qr_buf:
     flush_qr()
   # 解码 URL
   if '二维码解码URL' in body:
    flush_qr()
    state['qrcode_url']=body.split('二维码解码URL:',1)[-1].strip()
    state['updated_at']=time.time()
    _write_state(state)
   # 登录成功标识（"登录成功"、OneBot 启动、或已开始收发消息）
   if '登录成功' in body or ('OneBot' in body and '启动' in body) or '接收 <- ' in body or '发送 -> ' in body:
    state['online']=True
    state['updated_at']=time.time()
    _write_state(state)
  child.wait();service_log.event('qq-helper','QQ Helper exited code='+str(child.returncode))
 finally:
  if child and child.poll() is None:
   child.terminate()
   try:child.wait(timeout=10)
   except subprocess.TimeoutExpired:child.kill();child.wait()
  pid.unlink(missing_ok=True);(work/'qq-helper-child.pid').unlink(missing_ok=True);lock.close()
if __name__=='__main__':
 command=sys.argv[1:]
 if command[:1]==['--']:command=command[1:]
 if not command:raise SystemExit('Pass QQ executable and arguments after --')
 run(command)
