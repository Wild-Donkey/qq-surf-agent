"""Bounded, redacted rotating local service logs."""
import logging,logging.handlers,re,threading,os,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
_LOCK=threading.Lock()
ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
# 前置标签与短字段对齐宽度，让日志在等宽字体下列对齐、便于扫描。
_LABEL_WIDTH=30
_FIELD_WIDTHS={
 'group':12,'action':14,'proposed_action':14,'delivery':6,'batch':3,
 'kind':14,'user':12,'facts':3,'referents':3,'messages':3,'id':6,
 'code':24,'name':18,'result':16,'status':16,'operator_prompts':3,
 'candidates':3,'outgoing_moments':3,'forward_bundles':3,
}
_LABEL_RE=re.compile(r'^(\S+(?:\s+\S+)*?)\s+(\w+=.*)$')
_FIELD_RE=re.compile(r'\b(\w+)=(\S*)')
def clean(text):
 text=ANSI.sub('',str(text))
 text=re.sub(r'''(?i)((?:access[_ -]?token|api[_ -]?key|authorization|token|secret)["']?\s*[=:：]\s*["']?)([^\s,;"'}]+)''',r'\1[redacted]',text)
 text=re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?[redacted]',text)
 return ''.join(c for c in text if c in '\t\n' or ord(c)>=32)[:16000]
def align(text):
 """Pad the leading event label and common short fields so log columns line up.
 Only the first line is touched; multi-line bodies (tracebacks, JSON blobs)
 pass through unchanged. Values containing spaces are left as-is."""
 text=str(text)
 nl=text.find('\n')
 head=text if nl<0 else text[:nl]
 tail='' if nl<0 else text[nl:]
 m=_LABEL_RE.match(head)
 if not m:return text
 label=m.group(1)
 def pad(match):
  key,val=match.group(1),match.group(2)
  w=_FIELD_WIDTHS.get(key)
  if w is None or len(val)>=w:return match.group(0)
  return key+'='+val.ljust(w)
 rest=_FIELD_RE.sub(pad,m.group(2))
 return label.ljust(_LABEL_WIDTH)+' '+rest+tail
class SharedRotatingHandler(logging.handlers.RotatingFileHandler):
 def emit(self,record):
  # Separate CLI, HTTP and worker processes serialize rotation and reopen the active file.
  with open(self.baseFilename+'.lock','a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   try:
    if self.stream:self.stream.close();self.stream=None
    super().emit(record)
   finally:fcntl.flock(lock,fcntl.LOCK_UN)

def logger(name):
 with _LOCK:
  out=logging.getLogger('surf.'+name)
  if not out.handlers:
   directory=Path(os.environ.get('SURF_LOG_DIR',str(ROOT/'runtime'/'logs')));directory.mkdir(parents=True,exist_ok=True,mode=0o700)
   h=SharedRotatingHandler(directory/(name+'.log'),maxBytes=5_000_000,backupCount=5,encoding='utf-8')
   Path(h.baseFilename).chmod(0o600);h.setFormatter(logging.Formatter('%(asctime)s %(message)s','%m-%d %H:%M:%S'));out.addHandler(h);out.setLevel(logging.INFO);out.propagate=False
 return out
def event(name,text):
 try:logger(name).info(align(clean(text)))
 except Exception:pass  # Logging must not turn a confirmed send into a failed/retried turn.
