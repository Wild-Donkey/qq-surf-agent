"""Application-owned executable recovery actions; no model-supplied shell commands."""
import json,subprocess,sys,time
from pathlib import Path
import model_gate

ACTIONS=('none','retry_model','restart_worker','retry_profiling')
def perform(db,cfg,root,job,result):
 root=Path(root);alert=db.execute('SELECT code,details FROM alerts WHERE id=?',(job['alert_id'],)).fetchone();code=alert['code']
 action=result.get('action','none')
 if action=='none' and code in ('model_JSONDecodeError','model_worker_api_invalid_json','model_sse_event_invalid_json','model_profile_api_invalid_json'):action='retry_model'
 if action not in ACTIONS:return {'status':'blocked','message':'修复接口不存在。'}
 db.execute('CREATE TABLE IF NOT EXISTS repair_runs(job_id INTEGER PRIMARY KEY,action TEXT,status TEXT,updated REAL,result TEXT)');db.commit()
 previous=db.execute('SELECT status,result FROM repair_runs WHERE job_id=?',(job['id'],)).fetchone()
 if previous and previous['status'] in ('repaired','verified','blocked'):return json.loads(previous['result'])
 with db:db.execute("INSERT OR REPLACE INTO repair_runs VALUES (?,?,'running',?,'{}')",(job['id'],action,time.time()))
 try:
  if action=='retry_model':
   if not code.startswith('model_') or any(s in code for s in ('credit','quota','balance','unauthorized','invalid_api_key')):raise ValueError('此故障不能通过自动重试修复')
   test=root/'tests/test_protocol.py'
   if not test.exists():raise ValueError('缺少协议回归测试，未执行恢复')
   checked=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_protocol.py','-q'],cwd=root,capture_output=True,text=True,timeout=30)
   if checked.returncode:raise ValueError('协议回归测试未通过，未恢复调用')
   import social,application
   slot=model_gate.reserve(db,cfg)
   if slot is None:out={'status':'deferred','message':'等待模型调用空档后继续验证。'}
   else:
    try:
     raw,usage=social.generate_text(social.load_model(cfg),'连接恢复验证；只输出 {"call":{"name":"wait","arguments":{"reason":"健康检查"}}}。禁止发送消息。',{'health_check':True})
     decision=application.decision(application.decode(raw))
     if decision['action']!='wait':raise ValueError('模型健康检查未遵守接口')
     with db:
      db.execute("UPDATE model_calls SET status='ok',input_tokens=?,output_tokens=? WHERE id=?",(usage.get('prompt_tokens',0),usage.get('completion_tokens',0),slot))
      db.execute("INSERT OR REPLACE INTO metrics VALUES ('model_retry_after','0')")
      db.execute("INSERT OR REPLACE INTO metrics VALUES ('model_state','ready')")
     out={'status':'repaired','message':'协议回归测试及真实模型调用通过，已恢复自动处理。'}
    except Exception:
     with db:db.execute("UPDATE model_calls SET status='failed',error='repair_model_check_failed' WHERE id=?",(slot,))
     raise
  elif action=='restart_worker':
   if not code.startswith(('worker_','workflow_','delivery_','qq_')):raise ValueError('该故障不允许重启聊天进程')
   row=db.execute("SELECT value FROM metrics WHERE key='last_tick'").fetchone();stamp=float(row[0]) if row else 0
   if time.time()-stamp<30:out={'status':'verified','message':'已核验聊天周期正常推进，无需重复重启。'}
   else:
    start=time.time();(root/'runtime/workflow.restart').touch()
    out={'status':'blocked','message':'已请求重启，但尚未验证聊天周期恢复。'}
    for _ in range(60):
     time.sleep(1);row=db.execute("SELECT value FROM metrics WHERE key='last_tick'").fetchone()
     if row and float(row[0])>start:out={'status':'repaired','message':'已重启聊天进程，并确认新进程正常推进消息处理。'};break
  elif action=='retry_profiling':
   if not code.startswith(('group_bootstrap','profile_')):raise ValueError('该故障不允许重建画像任务')
   import profiling
   details=json.loads(alert['details'] or '{}');group=str(details.get('group_id',''))
   if group not in {str(g['id']) for g in cfg.get('groups',[])}:raise ValueError('故障记录缺少可核验的群号，未猜测修复对象')
   profiling.ensure_schema(db)
   previous=db.execute('SELECT status FROM profile_jobs WHERE group_id=?',(group,)).fetchone()
   profiling.enqueue(db,cfg,group,reset=bool(previous and previous['status']=='empty'))
   out={'status':'repaired','message':'已恢复该群批量补读任务；进度会继续落盘。'}
  else:out={'status':'blocked','message':'尚无已验证的修复动作；未擅自执行建议中的命令。'}
 except Exception as exc:out={'status':'blocked','message':'修复未通过验证：'+(str(exc)[:140] if isinstance(exc,ValueError) else type(exc).__name__)}
 with db:db.execute('UPDATE repair_runs SET status=?,updated=?,result=? WHERE job_id=?',(out['status'],time.time(),json.dumps(out,ensure_ascii=False),job['id']))
 return out
