import json,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workflow,social

class FreshnessTests(unittest.TestCase):
    def test_new_message_cancels_old_reply_before_send(self):
        db=workflow.connect(':memory:');now=time.time()
        cfg={'bot_id':'99','groups':[{'id':'1','name':'A'}],'mode':'live','reply_delay_seconds':0}
        with db:db.execute('INSERT INTO group_activity VALUES (?,?)',('1',now))
        job={'target_group':'1','event_key':'1:old','source_group':'1'}
        social.apply_decision(db,job,{'action':'reply','text':'旧回复','_context_watermark':now-2},{},cfg,now-1)
        with db:db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('1','new','fp','话题已更新','[]',now))
        with patch('agent.onebot_call',return_value={'user_id':99}) as api:
            self.assertEqual(workflow.dispatch(db,cfg,{},now+1),0)
            self.assertEqual(api.call_count,1)
        self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],'superseded');db.close()
    def test_own_messages_do_not_invalidate_snapshot(self):
        db=workflow.connect(':memory:')
        with db:db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('1','bot:sent','fp','自己发言','[]',1001))
        self.assertFalse(workflow.newer_context(db,'1',1000));db.close()
    def test_send_refreshes_actual_qq_history_before_old_draft(self):
        db=workflow.connect(':memory:');now=time.time()
        cfg={'bot_id':'99','groups':[{'id':'1','name':'A'}],'mode':'live','reply_delay_seconds':0,'refresh_before_send':True}
        with db:db.execute('INSERT INTO group_activity VALUES (?,?)',('1',now))
        social.apply_decision(db,{'target_group':'1','event_key':'1:old','source_group':'1'},
                              {'action':'reply','text':'旧草稿','_context_watermark':now-2},{},cfg,now-1)
        new={'message_id':9,'user_id':7,'time':now,'message':[{'type':'text','data':{'text':'最新话题'}}]}
        with patch('agent.onebot_call',side_effect=[{'user_id':99},{'messages':[new]}]) as api:
            self.assertEqual(workflow.dispatch(db,cfg,{},now+1),0)
            self.assertEqual(api.call_count,2)
        self.assertEqual(db.execute('SELECT text FROM events').fetchone()[0],'最新话题')
        self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],'superseded');db.close()
