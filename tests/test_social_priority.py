import tempfile,time,unittest
from unittest.mock import patch
import workflow,social,participants,model_gate
class SocialPriorityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','groups':[{'id':'1','name':'原群'},{'id':'2','name':'隔壁'}],'context_dir':self.tmp.name,'interaction':{'enabled':True,'quiet_seconds':0,'max_wait_seconds':20},'human_presence':{'enabled':True}}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def add(self,group,mid,stamp):
  workflow.ingest(self.db,{'post_type':'message','message_type':'group','group_id':group,'user_id':7,'message_id':mid,'time':stamp,'message':[{'type':'text','data':{'text':'自然聊天'}}]},self.cfg,stamp)
 def test_inactive_neighbor_is_not_enqueued_but_source_is_always_read(self):
  self.add(2,1,self.now-400);self.add(1,2,self.now-10)
  self.assertEqual([r[0] for r in self.db.execute("SELECT target_group FROM social_jobs WHERE event_key='1:2'")],['1'])
 def test_local_job_precedes_older_cross_group_copy(self):
  self.add(2,1,self.now-30);self.add(1,2,self.now-20)
  with self.db:
   self.db.execute("UPDATE social_jobs SET status='decided'")
   self.db.execute("INSERT INTO social_jobs(event_key,target_group,source_group,user_id,created,status) VALUES ('1:2','2','1','7',?,'pending')",(self.now-15,))
   self.db.execute("UPDATE social_jobs SET status='pending',created=? WHERE event_key='1:2' AND target_group='1'",(self.now-10,))
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model',return_value=({'action':'wait','text':''},{})) as call:social.tick(self.db,self.cfg)
  self.assertEqual(self.db.execute("SELECT status FROM social_jobs WHERE event_key='1:2' AND target_group='1'").fetchone()[0],'decided')
  self.assertEqual(self.db.execute("SELECT status FROM social_jobs WHERE event_key='1:2' AND target_group='2'").fetchone()[0],'cross_group_trigger_retired')
  self.assertEqual(call.call_count,1)
 def test_stale_neighbor_copies_are_drained_without_model_calls(self):
  self.add(2,1,self.now-20);self.add(1,2,self.now-10)
  with self.db:
   self.db.execute("UPDATE social_jobs SET status='decided'")
   self.db.execute("INSERT INTO social_jobs(event_key,target_group,source_group,user_id,created,status) VALUES ('1:2','2','1','7',?,'pending')",(self.now-10,))
   self.db.execute("DELETE FROM speaker_events WHERE group_id='2'")
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model') as call:social.tick(self.db,self.cfg);call.assert_not_called()
  self.assertEqual(self.db.execute("SELECT status FROM social_jobs WHERE event_key='1:2' AND target_group='2'").fetchone()[0],'cross_group_trigger_retired')

 def test_background_profiling_yields_to_pending_local_chat(self):
  self.add(1,1,self.now-10)
  self.assertIsNone(model_gate.reserve(self.db,self.cfg,self.now,background=True))
  self.assertIsNotNone(model_gate.reserve(self.db,self.cfg,self.now))
