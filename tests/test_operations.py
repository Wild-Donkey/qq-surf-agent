"""alerts, diagnostics, usage, onboarding regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import patch
import alerts
import workflow
import json
import tempfile
import time
import diagnostics
import usage_report
import onboarding
from memory import MarkdownMemory


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


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.dbpath=self.root/'db';db=workflow.connect(self.dbpath);db.close()
        self.db=diagnostics.connect(self.dbpath)
        self.cfg={'diagnostics':{'enabled':True,'cooldown_seconds':3600}}
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_worker_deduplicates_and_never_diagnoses_its_own_notifications(self):
        alerts.record(self.db,'worker_ValueError','任务异常',now=1000)
        alerts.record(self.db,'usage_report','用量',now=1000)
        alerts.record(self.db,'diagnostic_failed','排障失败',now=1000)
        diagnostics.enqueue(self.db,self.cfg,1001);diagnostics.enqueue(self.db,self.cfg,1002)
        self.assertEqual(self.db.execute('SELECT count(*) FROM diagnostic_jobs').fetchone()[0],1)
    def test_snapshot_omits_secrets_and_worker_records_result_usage(self):
        (self.root/'workflow.py').write_text('# public code')
        (self.root/'llm.local.json').write_text('{"api_key":"never-copy"}')
        alerts.record(self.db,'worker_ValueError','任务异常',now=1000)
        result={'cause':'配置项不匹配','evidence':'快照中的读取分支','next_step':'修正配置读取'}
        with patch('diagnostics.run_codex',return_value=(result,{'input_tokens':100,'output_tokens':20})):
            self.assertTrue(diagnostics.tick(self.db,self.cfg,self.root,1001))
        self.assertEqual(self.db.execute('SELECT status FROM diagnostic_jobs').fetchone()[0],'done')
        self.assertEqual(self.db.execute('SELECT input_tokens,output_tokens FROM model_calls').fetchone()[:],(100,20))
        directory=self.root/'runtime/diagnostics/1'
        self.assertTrue((directory/'incident.json').exists());self.assertFalse((directory/'llm.local.json').exists())
        diagnostics.enqueue(self.db,self.cfg,1002)
        self.assertEqual(self.db.execute('SELECT count(*) FROM diagnostic_jobs').fetchone()[0],1)
    def test_failed_diagnostics_does_not_recurse(self):
        alerts.record(self.db,'worker_ValueError','任务异常',now=1000)
        with patch('diagnostics.run_codex',side_effect=ValueError('codex_run_failed_1')):diagnostics.tick(self.db,self.cfg,self.root,1001)
        diagnostics.enqueue(self.db,self.cfg,1002)
        self.assertEqual(self.db.execute('SELECT count(*) FROM diagnostic_jobs').fetchone()[0],1)

    def test_mute_notice_does_not_launch_diagnosis(self):
        alerts.record(self.db,'bot_muted','已暂停',now=1000)
        diagnostics.enqueue(self.db,self.cfg,1001)
        self.assertEqual(self.db.execute('SELECT count(*) FROM diagnostic_jobs').fetchone()[0],0)
    def test_diagnostics_respects_shared_qps(self):
        alerts.record(self.db,'worker_ValueError','任务异常',now=1000)
        with self.db:self.db.execute("INSERT INTO model_calls(created,status) VALUES (1000,'ok')")
        with patch('diagnostics.run_codex') as run:
            self.assertFalse(diagnostics.tick(self.db,self.cfg,self.root,1001))
            run.assert_not_called()
        self.assertEqual(self.db.execute('SELECT status FROM diagnostic_jobs').fetchone()[0],'pending')


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


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cfg={'bot_id':'99','groups':[{'id':'1','name':'A'},{'id':'2','name':'B'}],'context_dir':str(self.root/'context'),'onboarding':{'enabled':True}}
        self.db=workflow.connect(':memory:');onboarding.ensure_schema(self.db);self.now=time.time()
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_discovery_distinguishes_existing_groups_from_new(self):
        path=self.root/'workflow.json';path.write_text(json.dumps(self.cfg))
        with patch('agent.onebot_call',side_effect=[{'user_id':99},[{'group_id':1,'group_name':'A'},{'group_id':2,'group_name':'B'},{'group_id':3,'group_name':'C'}]]):
            result=onboarding.discover(self.db,self.cfg,{},path,self.now)
        self.assertEqual(len(result['groups']),3)
        self.assertEqual(dict(self.db.execute('SELECT group_id,new_group FROM group_onboarding')),{'1':0,'2':0,'3':1})
    def test_scan_recognizes_known_account_distills_without_replaying_history(self):
        memory=MarkdownMemory(self.cfg['context_dir'])
        event={'message_type':'group','group_id':2,'user_id':7,'message_id':1,'time':self.now,'sender':{'nickname':'熟面孔'},'message':[{'type':'text','data':{'text':'聊技术'}}]}
        memory.observe(event,'99');memory.close()
        self.db.execute("INSERT INTO group_onboarding(group_id,created,status,new_group) VALUES ('1',?,'pending',1)",(self.now,));self.db.commit()
        event=dict(event,group_id=1,message_id=2)
        model={'enabled':True,'api_key':'test','model':'chosen'}
        with patch('agent.onebot_call',side_effect=[[{'user_id':7,'nickname':'熟面孔'}],{'messages':[event]}]),patch('social.load_model',return_value=model),patch('social.generate_text',return_value=(json.dumps({'summary':'经常讨论技术','style':'短句','topics':['技术']}),{'prompt_tokens':10,'completion_tokens':5})):
            onboarding.bootstrap(self.db,self.cfg,{},self.now)
        text=(self.root/'context/groups/1/group.md').read_text()
        self.assertIn('熟面孔',text);self.assertIn('经常讨论技术',text)
        self.assertEqual(self.db.execute('SELECT count(*) FROM social_jobs').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
        with patch('social.load_model',return_value=model):
            onboarding.greet(self.db,self.cfg,self.now);onboarding.greet(self.db,self.cfg,self.now+1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
        text=self.db.execute('SELECT text FROM candidates').fetchone()[0]
        self.assertIn('chosen',text)
        self.assertIn('https://github.com/Wild-Donkey/qq-surf-agent',text)
        self.assertIn('AI',text)
    def test_dormant_new_group_is_not_greeted(self):
        self.db.execute("INSERT INTO group_onboarding(group_id,created,status,new_group) VALUES ('1',?,'done',1)",(self.now,))
        self.db.execute('INSERT INTO group_activity VALUES (?,?)',('1',self.now-15*86400));self.db.commit()
        with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}):onboarding.greet(self.db,self.cfg,self.now)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)

    def test_empty_scan_later_history_allows_greeting(self):
        self.db.execute("INSERT INTO group_onboarding(group_id,created,status,new_group) VALUES ('1',?,'done',1)",(self.now,));self.db.commit()
        event={'group_id':1,'user_id':7,'message_id':10,'time':self.now,'message':[{'type':'text','data':{'text':'欢迎'}}]}
        with patch('agent.onebot_call',return_value={'messages':[event]}):onboarding.refresh_greeting_activity(self.db,self.cfg,{},self.now)
        with patch('social.load_model',return_value={'enabled':True,'api_key':'test','model':'chosen'}):
            onboarding.greet(self.db,self.cfg,self.now)
            with self.db:self.db.execute("UPDATE deliveries SET status='expired'")
            onboarding.greet(self.db,self.cfg,self.now+10)
            onboarding.greet(self.db,self.cfg,self.now+11)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'approved')
        with self.db:self.db.execute("UPDATE deliveries SET status='uncertain'")
        with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}):onboarding.greet(self.db,self.cfg,self.now+12)
        self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'uncertain')

    def test_first_greeting_without_history_or_human_presence(self):
        with self.db:self.db.execute("INSERT INTO group_onboarding(group_id,created,status,new_group) VALUES ('1',?,'done',1)",(self.now,))
        self.cfg.update(mode='live',refresh_before_send=False,max_per_tick=1,human_presence={'enabled':True})
        with patch('social.load_model',return_value={'enabled':False,'model':'configured-model'}):onboarding.greet(self.db,self.cfg,self.now)
        def api(cfg,action,payload=None):
            return {'get_login_info':{'user_id':99},'get_group_list':[{'group_id':1}], 'get_group_member_list':[{'user_id':99,'shut_up_timestamp':0}], 'send_group_msg':{'message_id':55}}[action]
        with patch('agent.onebot_call',side_effect=api):
            self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
            self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+2),0)
        self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'sent')
    def test_greeting_does_not_send_after_departure(self):
        with self.db:self.db.execute("INSERT INTO group_onboarding(group_id,created,status,new_group) VALUES ('1',?,'done',1)",(self.now,))
        self.cfg.update(mode='live')
        with patch('social.load_model',return_value={'model':'configured-model'}):onboarding.greet(self.db,self.cfg,self.now)
        def api(cfg,action,payload=None):return {'get_login_info':{'user_id':99},'get_group_list':[]}[action]
        with patch('agent.onebot_call',side_effect=api) as calls:self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),0)
        self.assertFalse(any(c.args[1]=='send_group_msg' for c in calls.call_args_list))


if __name__ == '__main__':
    unittest.main()
