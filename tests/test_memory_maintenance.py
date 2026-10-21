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
 def test_atomic_bundle_rejects_wrong_evidence(self):
  self.record('a');store=memory_store.MemoryStore(self.tmp.name)
  try:
   with self.assertRaises(ValueError):mm.apply(store,'1',{'group':{'state':{'summary':'must not commit'}},'members':[{'user_id':'2','state':{'current_topics':[{'text':'x','evidence':['1:fake'],'confidence':1}]}}]},[{'event_key':'1:a','speaker':'2'}])
   self.assertEqual(store.load('group','1')['revision'],0)
  finally:store.close()

if __name__=='__main__':unittest.main()
