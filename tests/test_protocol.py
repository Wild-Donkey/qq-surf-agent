"""protocol, empty_model_output regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import io
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from app import protocol
from app import application
from app import chatgpt_auth
from app import workflow
from app import repairs
from app import alerts
from app import diagnostics
from app import social
from app.protocol import ProtocolError
import urllib.error


class ProtocolTests(unittest.TestCase):
 def test_json_fence_is_unwrapped_but_prose_is_not_executed(self):
  self.assertEqual(application.decode('```json\n{"call":{"name":"wait","arguments":{}}}\n```')['name'],'wait')
  with self.assertRaises(protocol.ProtocolError) as error:application.decode('explanation before {"call":{}}')
  self.assertEqual(error.exception.stage,'worker_api')
 def test_sse_error_is_separate_and_does_not_log_contents(self):
  content='private-chat-must-not-be-logged'
  with patch('app.chatgpt_auth.access_token',return_value='test'),patch('app.chatgpt_auth.urllib.request.urlopen',return_value=io.BytesIO(('data: '+content+'\n\n').encode())):
   with self.assertRaises(protocol.ProtocolError) as error:chatgpt_auth.infer('test','test',{})
  self.assertEqual(error.exception.stage,'sse_event');self.assertNotIn(content,json.dumps(error.exception.details))
 def test_executable_repair_verifies_and_resumes_without_group_send(self):
  with tempfile.TemporaryDirectory() as root:
   root=Path(root);(root/'tests').mkdir();(root/'tests/test_protocol.py').write_text('# fixture')
   db=workflow.connect(':memory:');alerts.record(db,'model_worker_api_invalid_json','格式错误',now=1000)
   job={'id':1,'alert_id':db.execute('SELECT id FROM alerts').fetchone()[0]};cfg={'bot_id':'99'}
   result={'action':'retry_model'}
   with patch('app.repairs.subprocess.run',return_value=type('Result',(),{'returncode':0})()),patch('app.social.load_model',return_value={}),patch('app.social.generate_text',return_value=('{"call":{"name":"wait","arguments":{}}}',{'prompt_tokens':5})),patch('app.agent.onebot_call') as send:
    out=repairs.perform(db,cfg,root,job,result);self.assertEqual(out['status'],'repaired');send.assert_not_called()
    again=repairs.perform(db,cfg,root,job,result);self.assertEqual(again,out)
   self.assertEqual(db.execute("SELECT value FROM metrics WHERE key='model_state'").fetchone()[0],'ready');db.close()
 def test_quota_errors_are_not_blindly_retried(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');alerts.record(db,'model_credit_balance_exhausted','额度不足',now=1000)
   with patch('app.social.generate_text') as infer:
    out=repairs.perform(db,{},root,{'id':1,'alert_id':1},{'action':'retry_model'});self.assertEqual(out['status'],'blocked');infer.assert_not_called()
   db.close()


class EmptyOutputTests(unittest.TestCase):
 def test_empty_output_reports_finish_reason_without_response_content(self):
  model={'enabled':True,'provider':'compatible','model':'test','base_url':'http://localhost'}
  response={'choices':[{'message':{'content':''},'finish_reason':'length'}],'usage':{'completion_tokens':4096}}
  with patch('app.social.api_key',return_value='fake'),patch('app.vision.prepare',return_value=({'vision_status':{'attached':[],'unavailable':[]}},[])),patch('app.agent.request',return_value=json.dumps(response)):
   with self.assertRaises(ProtocolError) as caught:social.generate_text(model,'JSON',{})
  self.assertEqual(caught.exception.code,'model_empty_output');self.assertEqual(caught.exception.details['finish_reason'],'length')

 def test_content_filter_is_distinct_from_json_or_empty_output(self):
  model={'enabled':True,'provider':'compatible','model':'test','base_url':'http://localhost'}
  for content in ('','{"partial":true}'):
   response={'choices':[{'message':{'content':content},'finish_reason':'content_filter'}]}
   with patch('app.social.api_key',return_value='fake'),patch('app.vision.prepare',return_value=({'vision_status':{'attached':[],'unavailable':[]}},[])),patch('app.agent.request',return_value=json.dumps(response)):
    with self.assertRaises(ProtocolError) as caught:social.generate_text(model,'JSON',{})
   self.assertEqual(caught.exception.code,'model_content_filtered')

 def test_filtered_group_batch_is_blocked_and_cannot_be_requeued_as_pending(self):
  import time;from app import workflow,operator_control
  db=workflow.connect(':memory:');cfg={'groups':[{'id':1}],'bot_id':99}
  try:
   key=operator_control.submit(db,cfg,'1','test')
   job=db.execute("SELECT * FROM social_jobs WHERE event_key=?",('operator:'+key,)).fetchone()
   with db:slot=db.execute("INSERT INTO model_calls(created,status) VALUES (?,'reserved')",(time.time(),)).lastrowid
   social.block_filtered_batch(db,job,[job],slot,time.time());operator_control.wake(db)
   self.assertEqual(db.execute('SELECT status FROM social_jobs').fetchone()[0],'content_filtered')
   self.assertEqual(db.execute('SELECT status FROM operator_prompts').fetchone()[0],'blocked')
  finally:db.close()


class EnvelopeTests(unittest.TestCase):

    def test_nested_envelopes_and_conflicts(self):
        good = {'action': 'reply', 'text': '好', 'quality': 'natural'}
        self.assertEqual(social.validate_decision(json.dumps({'type': 'json_object', 'content': json.dumps(good)}))['action'], 'reply')
        # Conflicting value for a recognized field still raises.
        with self.assertRaises(ValueError):
            application.decode(json.dumps({'call': {'name': 'finish_turn', 'arguments': {'action': 'wait', 'quality': 'awkward'}}, 'quality': 'natural'}))
        # Unknown top-level fields are ignored, not rejected.
        result = application.decode(json.dumps({'call': {'name': 'wait', 'arguments': {}}, 'action': 'reply'}))
        self.assertEqual(result['name'], 'wait')

    def test_safe_misplaced_fields_are_normalized_without_overriding(self):
        raw = {'call': {'name': 'finish_turn', 'arguments': {'action': 'reply', 'text': '好'}}, 'quality': 'natural', 'group_state': {'summary': '聊天中'}, 'reason': '自然接话'}
        result = social.validate_decision(json.dumps(raw))
        self.assertEqual(result['action'], 'reply')
        self.assertEqual(result['state_updates']['group_state']['summary'], '聊天中')
        raw['action'] = 'wait'
        with self.assertRaises(social.ProtocolError):
            social.validate_decision(json.dumps(raw))

    def test_unknown_fields_are_ignored_not_rejected(self):
        # Unknown top-level fields are dropped; the valid call still succeeds.
        result = social.validate_decision('{"call":{"name":"wait","arguments":{}},"unexpected":"private text"}')
        self.assertEqual(result['action'], 'wait')
        self.assertNotIn('private text', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
