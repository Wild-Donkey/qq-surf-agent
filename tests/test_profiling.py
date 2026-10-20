import tempfile,time,json,unittest
from pathlib import Path
from unittest.mock import patch
import workflow,profiling,archive,application,model_gate
class ProfilingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.cfg={'context_dir':self.tmp.name,'bot_id':'99','groups':[{'id':'1','name':'A'}],'profiling':{'max_messages':5000}}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def event(self,mid,stamp,user=7):return {'group_id':1,'message_id':mid,'message_seq':mid,'user_id':user,'time':stamp,'sender':{'nickname':'群友'+str(user)},'message':[{'type':'text','data':{'text':'完整原话'+str(mid)+'甲'*300}}]}
 def test_pagination_preserves_full_history_and_detects_repeat(self):
  profiling.enqueue(self.db,self.cfg,'1');state={'phase':'fetch'};now=time.time();events=[self.event(900,now-100),self.event(1,now)]
  with patch('agent.onebot_call',return_value={'messages':events}) as api:
   profiling.fetch_page(self.db,self.cfg,{},'1',state);self.assertEqual(state['cursor'],'900')
   profiling.fetch_page(self.db,self.cfg,{},'1',state)
   self.assertEqual(api.call_args[0][2]['message_seq'],'900')
  self.assertEqual(state['total'],2);self.assertEqual(state['phase'],'group_map')
 def test_group_written_before_members_and_all_chunks_consumed(self):
  now=time.time();disk=archive.Archive(self.tmp.name)
  for i in range(150):disk.record(self.event(i+1,now-200+i,7+i%2))
  disk.close();profiling.enqueue(self.db,self.cfg,'1')
  rows=profiling.snapshot(self.cfg,'1');state={'phase':'group_map','pages':2,'total':len(rows),'messages':rows,'group_chunks':profiling.chunks(rows),'group_notes':[],'chunk_index':0}
  profiling.save(self.db,self.cfg,'1',state)
  group_seen=[];member_seen=[]
  def invoke(operation,payload):
   if operation==profiling.GROUP_MAP and payload['scope']=='group':group_seen.extend(r['event_key'] for r in payload['messages'])
   if operation==profiling.MEMBER_FINAL:
    self.assertTrue((Path(self.tmp.name)/'groups/1/group.md').exists());self.assertIn('综合群画像',payload['group_profile']);member_seen.append(payload['target_user_id'])
   return '综合群画像' if operation==profiling.GROUP_FINAL else '完整成员画像' if operation==profiling.MEMBER_FINAL else '分块观察与证据'
  for _ in range(30):
   profiling.tick(self.db,self.cfg,{},invoke)
   if self.db.execute('SELECT status FROM profile_jobs').fetchone()[0]=='done':break
  self.assertEqual(len(group_seen),150);self.assertEqual(len(set(group_seen)),150);self.assertEqual(set(member_seen),{'7','8'})
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
  self.assertIn('完整成员画像',application.member_markdown(self.cfg,'1','7'))
  self.assertNotIn('完整成员画像',application.member_markdown(self.cfg,'2','7'))
 def test_failed_chunk_resumes_without_advancing(self):
  profiling.enqueue(self.db,self.cfg,'1');state={'phase':'group_map','pages':1,'total':1,'messages':[],'group_chunks':[[{'speaker':'7','text':'hello'}]],'group_notes':[],'chunk_index':0};profiling.save(self.db,self.cfg,'1',state)
  def fail(*args):raise ValueError('failed')
  with self.assertRaises(ValueError):profiling.tick(self.db,self.cfg,{},fail)
  self.assertEqual(json.loads(profiling.state_path(self.cfg,'1').read_text())['chunk_index'],0)
 def test_background_and_chat_share_atomic_start_budget(self):
  first=model_gate.reserve(self.db,self.cfg,1000);self.assertIsNotNone(first)
  self.assertIsNone(model_gate.reserve(self.db,self.cfg,1001,background=True));self.assertIsNotNone(model_gate.reserve(self.db,self.cfg,1005,background=True))
