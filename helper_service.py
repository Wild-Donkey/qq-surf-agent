#!/usr/bin/env python3
"""Singleton QQ/NapCat wrapper. Closing the console leaves this process alive."""
import fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
import service_log
ROOT=Path(__file__).resolve().parent

def run(command):
 work=ROOT/'runtime';work.mkdir(exist_ok=True)
 lock=(work/'qq-helper.lock').open('w')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('QQ Helper wrapper already running')
 pid=work/'qq-helper-service.pid';pid.write_text(str(os.getpid()));child=None
 def stop(*_):
  if child and child.poll() is None:child.terminate()
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 try:
  service_log.event('qq-helper','Starting QQ Helper')
  child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,cwd=ROOT)
  (work/'qq-helper-child.pid').write_text(str(child.pid))
  while True:
   raw=child.stdout.readline(65536)
   if not raw:break
   service_log.event('qq-helper',raw.decode('utf-8',errors='replace').rstrip())
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
