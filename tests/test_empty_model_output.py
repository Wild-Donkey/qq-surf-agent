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
