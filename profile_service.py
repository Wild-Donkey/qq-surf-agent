#!/usr/bin/env python3
"""Independent application job runner; history profiling never blocks live chat."""
import argparse,fcntl,json,signal,threading,time
from pathlib import Path
import workflow,profiling,alerts,memory_maintenance
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
   profiling_enabled=cfg.get('profiling',{}).get('enabled',False)
   if not profiling_enabled and not cfg.get('memory_schema',{}).get('enabled'):break
   for group in cfg['groups']:
    if profiling_enabled and group.get('enabled',True):profiling.enqueue(db,cfg,str(group['id']))
   active=db.execute("SELECT group_id,phase FROM profile_jobs WHERE status IN ('queued','running') ORDER BY updated,group_id LIMIT 1").fetchone()
   api=json.loads((ROOT/'config.json').read_text())
   failed=False
   tasks=[('incremental_memory',lambda:memory_maintenance.tick(db,cfg))]
   if profiling_enabled:tasks.append(('history_profile',lambda:profiling.tick(db,cfg,api)))
   for stage,run in tasks:
    try:run()
    except Exception as exc:
     failed=True
     group=active['group_id'] if stage=='history_profile' and active else ''
     details={'group_id':group,'stage':stage,'error':type(exc).__name__}
     if hasattr(exc,'details'):details.update(exc.details)
     group=details.get('group_id',group)
     if stage=='history_profile' and active:
      with db:db.execute('UPDATE profile_jobs SET error=? WHERE group_id=?',(type(exc).__name__,group))
     code=getattr(exc,'code',type(exc).__name__)
     filtered=code=='model_content_filtered'
     alerts.record(db,'profile_content_filtered' if filtered else 'profile_'+type(exc).__name__,'服务商拒绝处理部分记忆样本；已记录缺口并停止重复调用，其余样本继续整理。' if filtered else '画像建档暂缓，正在自动排障。',scope=stage+':'+group,details=details)
     print(stage+' group='+group+' code='+code+' retryable='+str(not filtered)+' details='+json.dumps(details,ensure_ascii=False),flush=True)
   stop.wait(30 if failed else 1)
 finally:
  (ROOT/'runtime/profile-worker.pid').unlink(missing_ok=True);db.close();lock.close()
if __name__=='__main__':main()
