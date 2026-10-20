#!/usr/bin/env python3
"""Independent application job runner; history profiling never blocks live chat."""
import argparse,fcntl,json,signal,threading,time
from pathlib import Path
import workflow,profiling,alerts
ROOT=Path(__file__).resolve().parent

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,default=ROOT/'workflow.json');parser.add_argument('command',choices=['run','status','enqueue'],default='run',nargs='?');parser.add_argument('--group');parser.add_argument('--reset',action='store_true');args=parser.parse_args()
 cfg=json.loads(args.config.read_text());path=Path(cfg['database']);path=path if path.is_absolute() else ROOT/path
 db=workflow.connect(path);profiling.ensure_schema(db)
 if args.command=='enqueue':
  for group in cfg['groups']:
   if (args.group is None or str(group['id'])==args.group) and group.get('enabled',True):profiling.enqueue(db,cfg,str(group['id']),args.reset)
  print('画像任务已入队');db.close();return
 if args.command=='status':
  for row in db.execute('SELECT * FROM profile_jobs ORDER BY updated'):
   item=dict(row);progress=profiling.state_path(cfg,row['group_id'])
   if progress.exists():
    state=json.loads(progress.read_text());item.update(messages=state.get('total',state.get('fetched',0)),chunks_read=state.get('chunk_index',0),members_done=state.get('member_index',0),members_total=len(state.get('members',[])))
   print(json.dumps(item,ensure_ascii=False))
  db.close();return
 lock=(ROOT/'runtime/profile-worker.lock').open('w')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('画像任务进程已在运行')
 (ROOT/'runtime/profile-worker.pid').write_text(str(__import__('os').getpid()))
 stop=threading.Event();signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
 try:
  while not stop.is_set():
   cfg=json.loads(args.config.read_text())
   if not cfg.get('profiling',{}).get('enabled',False):break
   for group in cfg['groups']:
    if group.get('enabled',True):profiling.enqueue(db,cfg,str(group['id']))
   active=db.execute("SELECT group_id,phase FROM profile_jobs WHERE status IN ('queued','running') ORDER BY updated,group_id LIMIT 1").fetchone()
   try:
    api=json.loads((ROOT/'config.json').read_text());profiling.tick(db,cfg,api)
    stop.wait(1)
   except Exception as exc:
    group=active['group_id'] if active else ''
    details={'group_id':group,'stage':active['phase'] if active else 'scheduler','error':type(exc).__name__}
    if hasattr(exc,'details'):details.update(exc.details)
    if active:
     with db:db.execute('UPDATE profile_jobs SET error=? WHERE group_id=?',(type(exc).__name__,group))
    alerts.record(db,'profile_'+type(exc).__name__,'画像建档暂缓，正在自动排障。',scope=group,details=details)
    print('画像任务暂缓：'+type(exc).__name__,flush=True);stop.wait(30)
 finally:
  (ROOT/'runtime/profile-worker.pid').unlink(missing_ok=True);db.close();lock.close()
if __name__=='__main__':main()
