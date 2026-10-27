#!/usr/bin/env python3
"""Local operations: inspect, bootstrap memories, flush, test model, and request stop."""
import argparse
import json
from pathlib import Path
import time
import urllib.error
from app import agent
from app import alerts
from app.memory import MarkdownMemory
from app import social
from app import workflow
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['status','bootstrap','flush','model-check','stop'])
    args=parser.parse_args()
    cfg=json.loads((ROOT/'app.workflow.json').read_text())
    api=json.loads((ROOT/'config.json').read_text())
    path=Path(cfg['database'])
    if not path.is_absolute():path=ROOT/path
    path.parent.mkdir(parents=True,exist_ok=True)
    db=workflow.connect(path)
    try:
        if args.command=='stop':
            (ROOT/'runtime'/'app.workflow.stop').touch();print('已请求守护进程停止。');return
        if args.command=='status':
            for key in ['last_event','last_tick','model_state']:
                row=db.execute('SELECT value FROM metrics WHERE key=?',(key,)).fetchone()
                print(key+': '+(row[0] if row else '尚无记录'))
            print('groups:',len(cfg['groups']),'mode:',cfg.get('mode'))
            print('member memories:',len(list((Path(cfg['context_dir'])/'people').glob('*/people.md'))))
            print('pending alerts:',db.execute("select count(*) from alerts where status='pending'").fetchone()[0]);return
        if args.command=='model-check':
            try:
                model=social.load_model(cfg)
                decision,usage=social.call_model(model,{'memory':{'persona':'连接测试，保持安静'},'recent_target_messages':[],'candidates':[]})
                with db:
                    db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('model_state','ready'))
                    db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('model_retry_after','0'))
                print('模型调用成功，token用量：',usage.get('total_tokens'))
            except urllib.error.HTTPError as exc:
                error=json.loads(exc.read()).get('error',{})
                code=str(error.get('code') or error.get('type') or exc.code)
                print('模型调用失败：',code)
                alerts.record(db,'model_'+code,'模型连接测试失败；如为额度不足，请检查 API 项目余额。')
                alerts.flush(db,cfg,api)
                raise SystemExit(1)
            return
        memory=MarkdownMemory(cfg['context_dir'])
        try:
            if args.command=='bootstrap':
                cutoff=time.time()-cfg.get('inactive_group_days',14)*86400
                for group in cfg['groups']:
                    if not group.get('enabled',True):continue
                    try:
                        data=agent.onebot_call(api,'get_group_msg_history',{'group_id':int(group['id']),'count':30,'disable_get_url':True})
                        count=0;latest=0
                        for event in data.get('messages',[]):
                            if str(event.get('user_id'))==str(cfg['bot_id']):continue
                            stamp=float(event.get('time',0))
                            if not cutoff<=stamp<=time.time():continue
                            event.update(post_type='message',message_type='group',group_id=group['id'])
                            count+=int(memory.observe(event,cfg['bot_id']))
                            latest=max(latest,stamp)
                        if latest:
                            with db:db.execute('INSERT INTO group_activity VALUES (?,?) ON CONFLICT(group_id) DO UPDATE SET last_member_message=max(last_member_message,excluded.last_member_message)',(str(group['id']),latest))
                        print('样本观察：',group['name'],count,'条；不会创建投递任务',flush=True)
                    except Exception as exc:
                        print('来源暂不可读：',group['name'],type(exc).__name__,flush=True)
            memory.flush(cfg['groups']);print('本机 Markdown 记忆已更新。')
        finally:memory.close()
    finally:db.close()


if __name__=='__main__':main()
