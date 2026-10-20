from pathlib import Path
import sys,tempfile,time,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from memory import MarkdownMemory
from sessions import SessionPool
import qq,workflow

class SessionAndCLITests(unittest.TestCase):
    def test_idle_flush_releases_session(self):
        db=workflow.connect(':memory:')
        with tempfile.TemporaryDirectory() as tmp:
            memory=MarkdownMemory(tmp)
            cfg={'groups':[{'id':1,'name':'A'}],'interaction':{'idle_flush_seconds':300},'context_char_budget':100}
            with db:db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('1','event','fp','短句聊天','[]',1000))
            pool=SessionPool();pool.sync(db,cfg,1001)
            task=pool.task(db,cfg,{'target_group':'1','event_key':'1:abc'},1001)
            self.assertEqual(task.target_group,'1')
            self.assertEqual(pool.expire(memory,cfg,1299),[])
            self.assertEqual(pool.expire(memory,cfg,1300),['1'])
            self.assertEqual(pool.groups,{})
            self.assertIn('短句聊天',(Path(tmp)/'groups/1/group.md').read_text())
            memory.close()
        db.close()
    def test_manual_message_pauses_bot_and_records_receipt(self):
        db=workflow.connect(':memory:');now=time.time()
        with db:db.execute('INSERT INTO group_activity VALUES (?,?)',('1',now))
        cfg={'bot_id':'99','operator_pause_minutes':5}
        try:
            with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':123}]):
                qq.send(db,cfg,{},'1','我来接话')
            self.assertGreater(db.execute('select until_time from operator_pause').fetchone()[0],now)
            self.assertEqual(db.execute('select status from operator_log').fetchone()[0],'sent')
        finally:db.close()
    def test_manual_inactive_group_blocked_and_dry_run_does_not_send(self):
        db=workflow.connect(':memory:')
        try:
            with self.assertRaises(ValueError):qq.send(db,{'bot_id':'99'},{},'1','唤醒沉寂群',dry_run=True)
            with db:db.execute('INSERT INTO group_activity VALUES (?,?)',('1',time.time()))
            with patch('agent.onebot_call') as call:
                qq.send(db,{'bot_id':'99'},{},'1','预览',dry_run=True);call.assert_not_called()
        finally:db.close()

if __name__=='__main__':unittest.main()
