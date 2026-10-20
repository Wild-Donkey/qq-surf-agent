#!/usr/bin/env python3
"""Workspace-local supervisor: singleton, rotating logs, restart with backoff."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--config',type=Path,default=ROOT/'workflow.json')
parser.add_argument('--runtime',type=Path,default=ROOT/'runtime')
args=parser.parse_args()
WORK=args.runtime.resolve()
WORK.mkdir(parents=True,exist_ok=True)
lock=(WORK/'workflow-supervisor.lock').open('w')
try:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('服务已在运行')
lock.write(str(os.getpid()));lock.flush()
pidfile=WORK/'workflow-supervisor.pid'
pidfile.write_text(str(os.getpid()))
running=True
child=None

def stop(*_):
    global running
    running=False
    if child is not None and child.poll() is None:child.terminate()
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
delay=2
try:
    while running:
        log=WORK/'workflow.log'
        if log.exists() and log.stat().st_size>2_000_000:
            log.replace(WORK/'workflow.previous.log')
        started=time.monotonic()
        with log.open('ab',buffering=0) as out:
            out.write(('\n服务启动 '+time.strftime('%Y-%m-%d %H:%M:%S')+'\n').encode())
            child=subprocess.Popen([sys.executable,'-u',str(ROOT/'workflow.py'),'--config',str(args.config.resolve()),'run'],cwd=str(ROOT),stdout=out,stderr=subprocess.STDOUT)
            (WORK/'workflow-child.pid').write_text(str(child.pid))
            while child.poll() is None:
                if (WORK/'workflow.stop').exists():
                    (WORK/'workflow.stop').unlink()
                    stop()
                if not running:
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill()
                    break
                time.sleep(1)
        if not running:break
        if time.monotonic()-started>300:delay=2
        for _ in range(delay):
            if not running:break
            time.sleep(1)
        delay=min(delay*2,60)
finally:
    pidfile.unlink(missing_ok=True)
    lock.close()
