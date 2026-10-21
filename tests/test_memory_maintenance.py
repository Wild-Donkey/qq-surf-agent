import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import archive,memory_maintenance as mm,memory_store,workflow
from sessions import SessionPool,GroupSession

class MaintenanceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.cfg={'context_dir':self.tmp.name,'groups':[{'id':1,'name':'test'}],'memory_schema':{'enabled':True},'bot_id':'99','interaction':{}}
  self.db=workflow.connect(':memory:')
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def record(self,mid,user='2'):
  disk=archive.Archive(self.tmp.name)
  try:disk.record({'group_id':1,'message_id':mid,'user_id':user,'message':[{'type':'text','data':{'text':'喜欢编程'}}]})
  finally:disk.close()
 def job(self):
  db=mm.connect(self.cfg)
  try:return dict(db.execute('SELECT * FROM jobs').fetchone())
  finally:db.close()
 def test_periodic_persists_structured_group_and_person(self):
  self.record('a')
  def infer(payload):return {'group':{'state':{'summary':'讨论编程'}},'members':[{'user_id':'2','portrait':{'summary':'喜欢编程'}}]}
  self.assertTrue(mm.tick(self.db,self.cfg,invoke=infer))
  self.assertEqual(self.job()['watermark'],1)
  self.assertIn('喜欢编程',(Path(self.tmp.name)/'people/2/people.md').read_text())
  self.assertIn('讨论编程',(Path(self.tmp.name)/'groups/1/group.md').read_text())
  self.assertFalse(mm.tick(self.db,self.cfg,invoke=infer))
 def test_failed_update_retries_without_advancing(self):
  self.record('a');mm.enqueue(self.cfg,1,'shutdown')
  with self.assertRaises(ValueError):mm.tick(self.db,self.cfg,invoke=lambda p:{'members':[{'user_id':'3','state':{}}]})
  self.assertEqual(self.job()['watermark'],0);self.assertGreater(self.job()['next_attempt'],0)
 def test_new_enqueue_during_inference_not_lost(self):
  self.record('a');mm.enqueue(self.cfg,1,'before_compression')
  def infer(payload):
   self.record('b');mm.enqueue(self.cfg,1,'key_information');return {}
  mm.tick(self.db,self.cfg,invoke=infer)
  self.assertEqual((self.job()['watermark'],self.job()['target']),(1,2))
  mm.tick(self.db,self.cfg,invoke=lambda p:{})
  self.assertEqual(self.job()['watermark'],2)
 def test_before_trim_and_shutdown_checkpoint(self):
  self.record('a');pool=SessionPool();pool.cfg=self.cfg
  session=GroupSession('1',100,[{'text':'old info','time':1}]);pool.groups['1']=session
  pool._refresh(self.db,session,self.cfg)
  self.assertIn('before_compression',json.loads(self.job()['reasons']))
  from memory import MarkdownMemory
  memory=MarkdownMemory(self.tmp.name)
  try:pool.flush_active(memory)
  finally:memory.close()
  self.assertIn('shutdown',json.loads(self.job()['reasons']))
 def test_periodic_does_not_run_before_interval_and_keeps_new_records(self):
  self.record('a');mm.enqueue(self.cfg,1,'periodic',1000)
  self.record('b');mm.schedule(self.cfg,1100)
  self.assertEqual(self.job()['target'],1)
  mm.schedule(self.cfg,1300)
  self.assertEqual(self.job()['target'],2)
 def test_portrait_increment_preserves_prior_facts(self):
  self.record('a');self.record('b');store=memory_store.MemoryStore(self.tmp.name)
  try:
   store.portrait('1',{'topics':[{'text':'old','evidence':['1:a'],'confidence':1}]})
   mm.apply(store,'1',{'group':{'portrait':{'topics':[{'text':'new','evidence':['1:b'],'confidence':1}]}}},[{'event_key':'1:b','speaker':'2'}])
   self.assertEqual({f['text'] for f in store.load('group','1')['portrait']['topics']},{'old','new'})
  finally:store.close()
 def test_echoed_application_metadata_is_removed_not_trusted(self):
  self.record('a');store=memory_store.MemoryStore(self.tmp.name)
  try:
   fact={'text':'编程','evidence':['1:a'],'confidence':1,'first_seen':1,'last_seen':2,'source':'bridge','status':'supported'}
   mm.apply(store,'1',{'group':{'portrait':{'topics':[fact]}}},[{'event_key':'1:a','speaker':'2'}])
   saved=store.load('group','1')['portrait']['topics'][0]
   self.assertEqual(saved['source'],'incremental_memory');self.assertNotEqual(saved['first_seen'],1)
   with self.assertRaises(ValueError):mm.apply(store,'1',{'group':{'portrait':{'topics':[dict(fact,arbitrary='bad')]}}},[{'event_key':'1:a','speaker':'2'}])
  finally:store.close()
 def test_model_uses_fast_mode_and_input_only_schema(self):
  self.record('a')
  with patch('social.load_model',return_value={'provider':'compatible'}),patch('model_gate.reserve',return_value=1),patch('social.generate_text',return_value=('{}',{})) as infer:
   mm.tick(self.db,self.cfg)
  model,_,payload=infer.call_args.args
  self.assertEqual(model['reasoning_effort'],'none')
  self.assertNotIn('Person',payload['schema'])
 def test_provider_refusal_records_gap_without_repeating_same_batch(self):
  from protocol import ProtocolError
  self.record('a')
  with patch('social.load_model',return_value={}),patch('model_gate.reserve',return_value=1),patch('social.generate_text',side_effect=ProtocolError('model_response','model_content_filtered')):
   with self.assertRaises(ProtocolError):mm.tick(self.db,self.cfg)
  self.assertEqual(mm.status(self.cfg)['blocked_ranges'],1)
  self.assertEqual(mm.status(self.cfg)['pending_groups'],0)
  self.assertFalse(mm.tick(self.db,self.cfg,invoke=lambda p:self.fail('same refused batch retried')))
  self.record('b');mm.enqueue(self.cfg,1,'periodic')
  self.assertTrue(mm.tick(self.db,self.cfg,invoke=lambda p:{}))
 def test_failure_details_survive_later_success(self):
  self.record('a')
  with self.assertRaises(ValueError) as caught:mm.tick(self.db,self.cfg,invoke=lambda p:{'unexpected':'x'},now=1000)
  self.assertEqual(caught.exception.details['group_id'],'1')
  mm.tick(self.db,self.cfg,invoke=lambda p:{},now=1100)
  db=mm.connect(self.cfg)
  try:
   row=db.execute('SELECT error,details FROM failures').fetchone()
   self.assertIn('invalid_memory_bundle',row['error']);self.assertEqual(json.loads(row['details'])['group_id'],'1')
  finally:db.close()
 def test_atomic_bundle_rejects_wrong_evidence(self):
  self.record('a');store=memory_store.MemoryStore(self.tmp.name)
  try:
   with self.assertRaises(ValueError):mm.apply(store,'1',{'group':{'state':{'summary':'must not commit'}},'members':[{'user_id':'2','state':{'current_topics':[{'text':'x','evidence':['1:fake'],'confidence':1}]}}]},[{'event_key':'1:a','speaker':'2'}])
   self.assertEqual(store.load('group','1')['revision'],0)
  finally:store.close()

if __name__=='__main__':unittest.main()
