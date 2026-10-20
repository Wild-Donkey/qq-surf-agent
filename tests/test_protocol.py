import io,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import protocol,application,chatgpt_auth,workflow,repairs,alerts,diagnostics
class ProtocolTests(unittest.TestCase):
 def test_json_fence_is_unwrapped_but_prose_is_not_executed(self):
  self.assertEqual(application.decode('```json\n{"call":{"name":"wait","arguments":{}}}\n```')['name'],'wait')
  with self.assertRaises(protocol.ProtocolError) as error:application.decode('explanation before {"call":{}}')
  self.assertEqual(error.exception.stage,'worker_api')
 def test_sse_error_is_separate_and_does_not_log_contents(self):
  content='private-chat-must-not-be-logged'
  with patch('chatgpt_auth.access_token',return_value='test'),patch('chatgpt_auth.urllib.request.urlopen',return_value=io.BytesIO(('data: '+content+'\n\n').encode())):
   with self.assertRaises(protocol.ProtocolError) as error:chatgpt_auth.infer('test','test',{})
  self.assertEqual(error.exception.stage,'sse_event');self.assertNotIn(content,json.dumps(error.exception.details))
 def test_executable_repair_verifies_and_resumes_without_group_send(self):
  with tempfile.TemporaryDirectory() as root:
   root=Path(root);(root/'tests').mkdir();(root/'tests/test_protocol.py').write_text('# fixture')
   db=workflow.connect(':memory:');alerts.record(db,'model_worker_api_invalid_json','格式错误',now=1000)
   job={'id':1,'alert_id':db.execute('SELECT id FROM alerts').fetchone()[0]};cfg={'bot_id':'99'}
   result={'action':'retry_model'}
   with patch('repairs.subprocess.run',return_value=type('Result',(),{'returncode':0})()),patch('social.load_model',return_value={}),patch('social.generate_text',return_value=('{"call":{"name":"wait","arguments":{}}}',{'prompt_tokens':5})),patch('agent.onebot_call') as send:
    out=repairs.perform(db,cfg,root,job,result);self.assertEqual(out['status'],'repaired');send.assert_not_called()
    again=repairs.perform(db,cfg,root,job,result);self.assertEqual(again,out)
   self.assertEqual(db.execute("SELECT value FROM metrics WHERE key='model_state'").fetchone()[0],'ready');db.close()
 def test_quota_errors_are_not_blindly_retried(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');alerts.record(db,'model_credit_balance_exhausted','额度不足',now=1000)
   with patch('social.generate_text') as infer:
    out=repairs.perform(db,{},root,{'id':1,'alert_id':1},{'action':'retry_model'});self.assertEqual(out['status'],'blocked');infer.assert_not_called()
   db.close()
