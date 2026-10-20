import json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import diagnostics,workflow,alerts

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
