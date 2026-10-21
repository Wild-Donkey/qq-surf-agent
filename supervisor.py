#!/usr/bin/env python3
"""Workspace-local supervisor: singleton, rotating logs, restart with backoff."""
import argparse
import fcntl
import json
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
diagnostic=None
profile_child=None
profile_started=0
diagnostic_started=0

def start_diagnostic():
    global diagnostic,diagnostic_started
    cfg=json.loads(args.config.read_text())
    if not cfg.get('diagnostics',{}).get('enabled'):return
    log=(WORK/'diagnostics.log').open('ab',buffering=0)
    try:diagnostic=subprocess.Popen([sys.executable,'-u',str(ROOT/'diagnostics.py'),'--config',str(args.config.resolve())],cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT)
    finally:log.close()
    diagnostic_started=time.monotonic()

def start_profile():
    global profile_child,profile_started
    cfg=json.loads(args.config.read_text())
    if not (cfg.get('profiling',{}).get('enabled') or cfg.get('memory_schema',{}).get('enabled')):return
    log=(WORK/'profiling.log').open('ab',buffering=0)
    try:profile_child=subprocess.Popen([sys.executable,'-u',str(ROOT/'profile_service.py'),'--config',str(args.config.resolve()),'run'],cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT)
    finally:log.close()
    profile_started=time.monotonic()

def stop(*_):
    global running
    running=False
    if child is not None and child.poll() is None:child.terminate()
    if profile_child is not None and profile_child.poll() is None:profile_child.terminate()
    if diagnostic is not None and diagnostic.poll() is None:diagnostic.terminate()
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
delay=2
try:
    start_diagnostic()
    start_profile()
    while running:
        log=WORK/'workflow.log'
        if log.exists() and log.stat().st_size>2_000_000:
            log.replace(WORK/'workflow.previous.log')
        planned_restart=False
        started=time.monotonic()
        with log.open('ab',buffering=0) as out:
            out.write(('\n服务启动 '+time.strftime('%Y-%m-%d %H:%M:%S')+'\n').encode())
            child=subprocess.Popen([sys.executable,'-u',str(ROOT/'workflow.py'),'--config',str(args.config.resolve()),'run'],cwd=str(ROOT),stdout=out,stderr=subprocess.STDOUT)
            (WORK/'workflow-child.pid').write_text(str(child.pid))
            while child.poll() is None:
                if profile_child is not None and profile_child.poll() is not None and time.monotonic()-profile_started>60:start_profile()
                if diagnostic is not None and diagnostic.poll() is not None and time.monotonic()-diagnostic_started>60:start_diagnostic()
                if (WORK/'workflow.restart').exists():
                    (WORK/'workflow.restart').unlink();planned_restart=True;child.terminate()
                if (WORK/'workflow.stop').exists():
                    (WORK/'workflow.stop').unlink()
                    stop()
                if not running:
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill()
                    break
                time.sleep(1)
        if not running:break
        if planned_restart:delay=2;continue
        try:
            import alerts,workflow
            cfg=json.loads(args.config.read_text());database=Path(cfg['database'])
            if not database.is_absolute():database=args.config.resolve().parent/database
            db=workflow.connect(database)
            alerts.record(db,'workflow_process_exit','聊天服务进程退出（'+str(child.returncode)+'），正在重启。')
            db.close()
        except Exception:pass
        if time.monotonic()-started>300:delay=2
        for _ in range(delay):
            if not running:break
            time.sleep(1)
        delay=min(delay*2,60)
finally:
    if profile_child is not None and profile_child.poll() is None:
        profile_child.terminate()
        try:profile_child.wait(timeout=10)
        except subprocess.TimeoutExpired:profile_child.kill()
    if diagnostic is not None and diagnostic.poll() is None:
        diagnostic.terminate()
        try:diagnostic.wait(timeout=10)
        except subprocess.TimeoutExpired:diagnostic.kill()
    pidfile.unlink(missing_ok=True)
    lock.close()
