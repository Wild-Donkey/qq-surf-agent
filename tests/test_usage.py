import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workflow,usage_report,alerts


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.db=workflow.connect(':memory:')
        self.cfg={'bot_id':'99','alerts':{'owner_qq':'123'},'usage_report':{'enabled':True,'interval_seconds':10800}}
    def tearDown(self):self.db.close()
    def test_interval_persisted_and_reports_exact_window_once(self):
        self.assertFalse(usage_report.tick(self.db,self.cfg,1000))
        for created,status,inputs,outputs in [(999,'ok',100,10),(1000,'ok',20,3),(2000,'failed',None,None),(3000,'reserved',None,None)]:
            self.db.execute('INSERT INTO model_calls(created,status,input_tokens,output_tokens) VALUES (?,?,?,?)',(created,status,inputs,outputs))
        self.db.commit()
        self.assertFalse(usage_report.tick(self.db,self.cfg,11799))
        self.assertTrue(usage_report.tick(self.db,self.cfg,11800))
        report=self.db.execute("SELECT * FROM alerts WHERE code='usage_report'").fetchone()
        self.assertIn('本期调用 3 次：成功 1，失败 1，待确认 1',report['summary'])
        self.assertIn('近24小时调用 4 次',report['summary'])
        self.assertIn('输入 20 / 输出 3 token',report['summary'])
        self.assertFalse(usage_report.tick(self.db,self.cfg,11801))
        self.assertEqual(self.db.execute('SELECT count(*) FROM alerts').fetchone()[0],1)
        with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':4}]) as call:
            self.assertEqual(alerts.flush(self.db,self.cfg,{},11802),1)
            args=call.call_args[0][2]
            self.assertEqual(args['user_id'],123)
            self.assertIn('bot 用量报告',args['message'][0]['data']['text'])
            self.assertNotIn('异常通知',args['message'][0]['data']['text'])
    def test_downtime_collapses_missed_periods_and_old_unsent_snapshot(self):
        usage_report.tick(self.db,self.cfg,1000)
        self.assertTrue(usage_report.tick(self.db,self.cfg,11800))
        self.assertTrue(usage_report.tick(self.db,self.cfg,100000))
        self.assertEqual(self.db.execute("SELECT count(*) FROM alerts WHERE status='pending'").fetchone()[0],1)
        self.assertFalse(usage_report.tick(self.db,self.cfg,100001))
    def test_disabled_does_not_schedule(self):
        self.cfg['usage_report']['enabled']=False
        self.assertFalse(usage_report.tick(self.db,self.cfg,1000))
        self.assertFalse(self.db.execute("SELECT value FROM metrics WHERE key='usage_report_window_start'").fetchone())


if __name__=='__main__':unittest.main()
