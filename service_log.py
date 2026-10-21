"""Bounded, redacted rotating local service logs."""
import logging,logging.handlers,re,threading,os,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parent
_LOCK=threading.Lock()
ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
def clean(text):
 text=ANSI.sub('',str(text))
 text=re.sub(r'''(?i)((?:access[_ -]?token|api[_ -]?key|authorization|token|secret)["']?\s*[=:：]\s*["']?)([^\s,;"'}]+)''',r'\1[redacted]',text)
 text=re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?[redacted]',text)
 return ''.join(c for c in text if c in '\t\n' or ord(c)>=32)[:16000]
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
 try:logger(name).info(clean(text))
 except Exception:pass  # Logging must not turn a confirmed send into a failed/retried turn.
