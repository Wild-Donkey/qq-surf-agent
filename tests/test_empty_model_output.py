import sys,json,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import social
from protocol import ProtocolError

class EmptyOutputTests(unittest.TestCase):
 def test_empty_output_reports_finish_reason_without_response_content(self):
  model={'enabled':True,'provider':'compatible','model':'test','base_url':'http://localhost'}
  response={'choices':[{'message':{'content':''},'finish_reason':'length'}],'usage':{'completion_tokens':4096}}
  with patch('social.api_key',return_value='fake'),patch('vision.prepare',return_value=({'vision_status':{'attached':[],'unavailable':[]}},[])),patch('agent.request',return_value=json.dumps(response)):
   with self.assertRaises(ProtocolError) as caught:social.generate_text(model,'JSON',{})
  self.assertEqual(caught.exception.code,'model_empty_output');self.assertEqual(caught.exception.details['finish_reason'],'length')

 def test_content_filter_is_distinct_from_json_or_empty_output(self):
  model={'enabled':True,'provider':'compatible','model':'test','base_url':'http://localhost'}
  for content in ('','{"partial":true}'):
   response={'choices':[{'message':{'content':content},'finish_reason':'content_filter'}]}
   with patch('social.api_key',return_value='fake'),patch('vision.prepare',return_value=({'vision_status':{'attached':[],'unavailable':[]}},[])),patch('agent.request',return_value=json.dumps(response)):
    with self.assertRaises(ProtocolError) as caught:social.generate_text(model,'JSON',{})
   self.assertEqual(caught.exception.code,'model_content_filtered')

 def test_filtered_group_batch_is_blocked_and_cannot_be_requeued_as_pending(self):
  import workflow,operator_control,time
  db=workflow.connect(':memory:');cfg={'groups':[{'id':1}],'bot_id':99}
  try:
   key=operator_control.submit(db,cfg,'1','test')
   job=db.execute("SELECT * FROM social_jobs WHERE event_key=?",('operator:'+key,)).fetchone()
   with db:slot=db.execute("INSERT INTO model_calls(created,status) VALUES (?,'reserved')",(time.time(),)).lastrowid
   social.block_filtered_batch(db,job,[job],slot,time.time());operator_control.wake(db)
   self.assertEqual(db.execute('SELECT status FROM social_jobs').fetchone()[0],'content_filtered')
   self.assertEqual(db.execute('SELECT status FROM operator_prompts').fetchone()[0],'blocked')
  finally:db.close()
