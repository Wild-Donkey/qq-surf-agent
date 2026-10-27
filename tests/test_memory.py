"""memory_store, memory_maintenance, archive regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import tempfile
import unittest
import time
from unittest.mock import patch
from app import memory_store as ms
from app import archive
from app import application
from app import memory_maintenance as mm
from app import memory_store
from app import workflow
from app.sessions import SessionPool
from app.sessions import GroupSession
from app.archive import Archive


class MemorySchemaTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=ms.MemoryStore(self.root)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def fact(self):return {'text':'喜欢被叫阿明','evidence':['1:10'],'confidence':.9}
 def record(self):
  disk=archive.Archive(self.root);disk.record({'group_id':1,'message_id':10,'user_id':7,'message':[{'type':'text','data':{'text':'以后叫我阿明'}}]});disk.close()
 def test_unknown_fields_and_app_metadata_are_not_worker_writable(self):
  for state in ({'system_prompt':'override'},{'summary':'ok','revision':99},{'current_topics':[{'text':'x','evidence':[],'confidence':.4,'source':'manual'}]}):
   with self.assertRaises(ValueError):self.store.state_updates('1',state,[])
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM documents').fetchone()[0],0)
 def test_fact_evidence_and_group_scope_are_checked(self):
  self.record();bad=self.fact();bad['evidence']=['1:404']
  with self.assertRaises(ValueError):self.store.state_updates('1',{'current_topics':[bad]},[])
  bad['evidence']=['2:10']
  with self.assertRaises(ValueError):self.store.state_updates('1',{'current_topics':[bad]},[])
  bad.update(evidence=[],confidence=1)
  with self.assertRaises(ValueError):self.store.state_updates('1',{'current_topics':[bad]},[])
 def test_patch_preserves_other_fields_first_seen_and_audit(self):
  self.record();self.store.state_updates('1',{'current_topics':[self.fact()]},[('7',{'summary':'现在想聊称呼'})])
  first=self.store.load('group','1')['state']['current_topics'][0]['first_seen']
  self.store.state_updates('1',{'summary':'当前仍在聊称呼'},[])
  doc=self.store.load('group','1');self.assertEqual(doc['state']['current_topics'][0]['first_seen'],first);self.assertEqual(doc['revision'],2)
  ms.validate(doc,ms.SCHEMA['$defs']['Group']);self.assertEqual(self.store.db.execute('SELECT count(*) FROM changes').fetchone()[0],2)
 def test_invalid_second_member_does_not_partially_commit(self):
  with self.assertRaises(ValueError):self.store.state_updates('1',{'summary':'not committed'},[('7',{'summary':'valid'}),('8',{'unknown_field':'invalid'})])
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM documents').fetchone()[0],0)
 def test_stale_revision_cannot_overwrite_new_state(self):
  stale=self.store.load('group','1');self.store.state_updates('1',{'summary':'new'},[])
  with self.assertRaisesRegex(ValueError,'memory_revision_conflict'):self.store.commit([stale],'worker')
  self.assertEqual(self.store.load('group','1')['state']['summary'],'new')
 def test_committed_state_survives_projection_failure(self):
  with patch('app.memory_store.atomic_write',side_effect=OSError('disk interruption')):
   with self.assertRaises(OSError):self.store.state_updates('1',{'summary':'durable'},[])
  self.store.materialize();doc=json.loads((self.root/'groups/1/group.json').read_text())
  self.assertEqual(doc['state']['summary'],'durable');self.assertIn('durable',(self.root/'groups/1/group.md').read_text())
 def test_existing_markdown_is_backed_up_and_migration_is_idempotent(self):
  path=self.root/'groups/1/group.md';path.parent.mkdir(parents=True);old='# 群记忆\n\n<!-- portrait:start -->\n## 常见话题\n- 技术讨论，证据1:10\n<!-- portrait:end -->\n\n## 手工补充\n请少反问\n';path.write_text(old)
  ms.migrate(self.root,[{'id':'1','name':'测试群'}]);doc=self.store.load('group','1');revision=doc['revision']
  ms.migrate(self.root,[{'id':'1','name':'测试群'}]);self.assertEqual(self.store.load('group','1')['revision'],revision)
  self.assertEqual(next((self.root/'legacy_memory').rglob('*.md')).read_text(),old);self.assertIn('请少反问',path.read_text())
 def test_canonical_member_memory_is_group_isolated(self):
  self.store.state_updates('1',None,[('7',{'summary':'群一的待办'},'group')]);self.store.state_updates('2',None,[('7',{'summary':'群二的私有状态'},'group')])
  text=application.member_markdown({'context_dir':str(self.root)},'1','7');self.assertIn('群一的待办',text);self.assertNotIn('群二的私有状态',text)
 def test_application_ingest_inject_and_structured_state_round_trip(self):
  from app import workflow,social
  cfg={'bot_id':'99','context_dir':str(self.root),'groups':[{'id':'1','name':'A'}],'interaction':{'enabled':True},'durable_archive':{'enabled':True},'memory_schema':{'enabled':True},'human_presence':{'known_humans':['7']}}
  db=workflow.connect(':memory:');event={'post_type':'message','message_type':'group','group_id':1,'message_id':10,'user_id':7,'time':time.time(),'sender':{'nickname':'阿明'},'message':[{'type':'text','data':{'text':'叫我阿明'}}]}
  workflow.ingest(db,event,cfg)
  payload={'member_profiles':[{'id':'7','current_judgment':{'kind':'human'}}]}
  application.update_states(cfg,'1',payload,{'group_state':{'summary':'讨论称呼','current_topics':[self.fact()]},'members':[{'user_id':'7','state':{'summary':'希望叫阿明'}}]},db)
  self.assertEqual(self.store.load('group','1')['state']['summary'],'讨论称呼')
  self.assertIn('希望叫阿明',application.member_context(cfg,'1',payload)[0]['markdown']);db.close()


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
  from app.memory import MarkdownMemory
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
  with patch('app.social.load_model',return_value={'provider':'compatible'}),patch('app.model_gate.reserve',return_value=1),patch('app.social.generate_text',return_value=('{}',{})) as infer:
   mm.tick(self.db,self.cfg)
  model,_,payload=infer.call_args.args
  self.assertEqual(model['reasoning_effort'],'none')
  self.assertNotIn('Person',payload['schema'])
 def test_provider_refusal_records_gap_without_repeating_same_batch(self):
  from app.protocol import ProtocolError
  self.record('a')
  with patch('app.social.load_model',return_value={}),patch('app.model_gate.reserve',return_value=1),patch('app.social.generate_text',side_effect=ProtocolError('model_response','model_content_filtered')):
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
 def test_atomic_bundle_skips_wrong_evidence(self):
  self.record('a');store=memory_store.MemoryStore(self.tmp.name)
  try:
   # Evidence '1:fake' is not in the current batch; it should be filtered
   # out and the fact dropped, not raise. Valid group state still commits.
   mm.apply(store,'1',{'group':{'state':{'summary':'committed ok'}},'members':[{'user_id':'2','state':{'current_topics':[{'text':'x','evidence':['1:fake'],'confidence':1}]}}]},[{'event_key':'1:a','speaker':'2'}])
   self.assertEqual(store.load('group','1')['state']['summary'],'committed ok')
   self.assertEqual(store.load('person','2')['state']['current_topics'],[])
  finally:store.close()


class ArchiveTests(unittest.TestCase):
 def test_permanent_restart_group_search_and_window(self):
  with tempfile.TemporaryDirectory() as root:
   a=Archive(root)
   e={'group_id':1,'message_id':2,'user_id':3,'time':time.time()-200*86400,'message':[{'type':'text','data':{'text':'阿黄猫在武汉'}}]}
   a.record(e);a.close();a=Archive(root)
   self.assertEqual(a.get('1','2')['text'],'阿黄猫在武汉')
   self.assertTrue(a.search('1','阿黄猫'));self.assertEqual(a.search('2','阿黄猫'),[])
   self.assertEqual(a.window('1',0)['total_messages'],1)
   a.add_visual('1','2','蓝色猫窝')
   self.assertTrue(a.search('1','猫窝'));a.close()
 def test_recall_episodes_relabels_legacy_qq_speakers(self):
  from app.memory import MarkdownMemory
  with tempfile.TemporaryDirectory() as tmp:
   memory=MarkdownMemory(tmp)
   try:
    self.assertTrue(memory.observe({'message_type':'group','group_id':1,'user_id':1234567,'message_id':1,
      'sender':{'nickname':'阿甲'},'message':[{'type':'text','data':{'text':'编程真有意思'}}]},99))
    with memory.db:
     memory.db.execute('INSERT INTO conversation_episodes VALUES (?,?,?,?,?)',
       ('k1','1',1,2,'1234567：聊了编程；7654321：别的事'))
    rows=memory.recall_episodes('1','编程')
    self.assertEqual(rows[0]['summary'],'阿甲：聊了编程；7654321：别的事')
   finally:memory.close()
 def test_backfill_is_enriched_by_original_mentions(self):
  with tempfile.TemporaryDirectory() as root:
   a=Archive(root);e={'group_id':1,'message_id':2,'user_id':'unknown','message':[{'type':'text','data':{'text':'清理上下文'}}],'archive_origin':'working_cache_backfill'}
   a.record(e);e.pop('archive_origin');e['user_id']='3';e['message'].append({'type':'at','data':{'qq':'9'}});a.record(e)
   import json
   saved=a.get('1','2');self.assertEqual(saved['user_id'],'3');self.assertEqual(json.loads(saved['mentions']),['9']);a.close()


if __name__ == '__main__':
    unittest.main()
