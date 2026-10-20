import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import alerts,workflow

class AlertTests(unittest.TestCase):
    def test_dedupe_and_owner_only(self):
        db=workflow.connect(':memory:')
        try:
            alerts.record(db,'quota','模型额度不足',now=1000)
            alerts.record(db,'quota','模型额度不足',now=1001)
            self.assertEqual(db.execute('select count from alerts').fetchone()[0],2)
            cfg={'bot_id':'99','alerts':{'owner_qq':'123','cooldown_seconds':1800}}
            with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':4}]) as call:
                self.assertEqual(alerts.flush(db,cfg,{},1002),1)
                self.assertEqual(call.call_args[0][2]['user_id'],123)
            alerts.record(db,'quota','模型额度不足',now=1100)
            with patch('agent.onebot_call') as call:
                self.assertEqual(alerts.flush(db,cfg,{},1101),0);call.assert_not_called()
            alerts.record(db,'quota','模型额度不足',now=3000)
            self.assertEqual(db.execute('select status from alerts').fetchone()[0],'pending')
        finally:db.close()
    def test_missing_owner_cannot_send(self):
        db=workflow.connect(':memory:')
        try:
            alerts.record(db,'error','异常')
            with patch('agent.onebot_call') as call:
                alerts.flush(db,{'bot_id':'99'},{})
                call.assert_not_called()
        finally:db.close()

if __name__=='__main__':unittest.main()
