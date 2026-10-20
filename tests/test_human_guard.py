import sys,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workflow,social,participants

class HumanGuardTests(unittest.TestCase):
    def test_approved_reply_cannot_send_without_recent_human(self):
        db=workflow.connect(':memory:');now=time.time()
        cfg={'bot_id':'99','mode':'live','reply_delay_seconds':0,'groups':[{'id':'1','name':'A'}],
             'human_presence':{'enabled':True,'known_bots':['8']}}
        with db:
            db.execute('INSERT INTO group_activity VALUES (?,?)',('1',now))
            participants.observe(db,{'group_id':1,'user_id':8,'message_id':1,'time':now},cfg,'自动播报',now)
        social.apply_decision(db,{'target_group':'1','source_group':'1','event_key':'1:1'},
                              {'action':'reply','text':'不该发出'}, {},cfg,now)
        with patch('agent.onebot_call',return_value={'user_id':99}) as api:
            self.assertEqual(workflow.dispatch(db,cfg,{},now+1),0)
            self.assertEqual(api.call_count,1)
        self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],'approved');db.close()
