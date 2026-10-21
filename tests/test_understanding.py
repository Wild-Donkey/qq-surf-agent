import tempfile,unittest,json,time
from pathlib import Path
from unittest.mock import patch
import understanding,application,workflow,archive

class UnderstandingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.cfg={'context_dir':self.tmp.name};self.db=workflow.connect(':memory:')
  self.payload={'request_slot':understanding.begin(self.cfg,'1',100,[{'event_key':'1:msg'}]),'_attached_images':[{'reference':'image1','group_id':'1','event_key':'1:msg'}]}
  disk=archive.Archive(self.tmp.name)
  try:disk.record({'group_id':1,'message_id':'msg','user_id':2,'message':[{'type':'text','data':{'text':'看图'}}]})
  finally:disk.close()
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_every_window_has_reserved_slot_and_retry_keeps_identity(self):
  slot=understanding.begin(self.cfg,'1',100,[{'event_key':'1:next'}]);self.assertEqual(slot['request_id'],self.payload['request_slot']['request_id']);self.assertIsNone(slot['understanding'])
  self.assertNotEqual(understanding.begin(self.cfg,'1',101,[])['request_id'],slot['request_id'])
 def test_wait_keeps_understanding_out_of_public_text_and_saves_context_log(self):
  value={'summary':'大家在看猫图','images':[{'reference':'image1','description':'一只猫趴在纸箱上'}],'uncertainties':['品种不确定']}
  call=application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'wait','understanding':value}}},ensure_ascii=False));decision=application.decision(call)
  self.assertEqual(decision['text'],'')
  with patch('service_log.event') as log:receipt=understanding.save(self.cfg,'1',self.payload,decision['understanding'],self.db)
  self.assertTrue(receipt['saved']);self.assertIn('一只猫',log.call_args.args[1]);self.assertEqual(understanding.recent(self.cfg,'2'),[])
  path=Path(self.tmp.name)/'groups/1/requests'/(receipt['request_id']+'.md');self.assertIn('一只猫',path.read_text())
  # A later summary-only update retains already understood images.
  understanding.save(self.cfg,'1',self.payload,{'summary':'话题已收尾'},self.db)
  self.assertEqual(len(understanding.recent(self.cfg,'1')[0]['understanding']['images']),1)
 def test_unknown_image_is_not_logged_or_saved_as_observed(self):
  with patch('service_log.event') as log:receipt=understanding.save(self.cfg,'1',self.payload,{'images':[{'reference':'invented','description':'未经看过的图'}]},self.db)
  self.assertEqual(receipt['rejected_images'],1);self.assertEqual(receipt['understanding']['images'],[]);self.assertNotIn('未经看过',log.call_args.args[1])
 def test_independent_from_invalid_person_state(self):
  understanding.save(self.cfg,'1',self.payload,{'summary':'局面观察'},self.db)
  result=application.apply_worker_states(self.cfg,'1',self.payload,{'members':[{'user_id':'not-in-context','state':'bad'}]},self.db)
  self.assertFalse(result['saved']);self.assertEqual(understanding.recent(self.cfg,'1')[0]['understanding']['summary'],'局面观察')
 def test_tool_receipt_updates_next_model_context(self):
  result=application.read(self.db,self.cfg,'1',self.payload,{'name':'record_understanding','arguments':{'understanding':{'summary':'短暂安静'}}})
  self.assertTrue(result['saved']);self.assertIn('understanding_context',self.payload)
  self.assertEqual(self.payload['request_slot']['status'],'saved')
