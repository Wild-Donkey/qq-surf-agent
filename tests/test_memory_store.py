import json,tempfile,unittest,time
from pathlib import Path
from unittest.mock import patch
import memory_store as ms,archive,application
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
  with patch('memory_store.atomic_write',side_effect=OSError('disk interruption')):
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
  import workflow,social
  cfg={'bot_id':'99','context_dir':str(self.root),'groups':[{'id':'1','name':'A'}],'interaction':{'enabled':True},'durable_archive':{'enabled':True},'memory_schema':{'enabled':True},'human_presence':{'known_humans':['7']}}
  db=workflow.connect(':memory:');event={'post_type':'message','message_type':'group','group_id':1,'message_id':10,'user_id':7,'time':time.time(),'sender':{'nickname':'阿明'},'message':[{'type':'text','data':{'text':'叫我阿明'}}]}
  workflow.ingest(db,event,cfg)
  payload={'member_profiles':[{'id':'7','current_judgment':{'kind':'human'}}]}
  application.update_states(cfg,'1',payload,{'group_state':{'summary':'讨论称呼','current_topics':[self.fact()]},'members':[{'user_id':'7','state':{'summary':'希望叫阿明'}}]},db)
  self.assertEqual(self.store.load('group','1')['state']['summary'],'讨论称呼')
  self.assertIn('希望叫阿明',application.member_context(cfg,'1',payload)[0]['markdown']);db.close()
