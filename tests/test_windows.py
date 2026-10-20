import json,tempfile,time,unittest
from unittest.mock import patch
import workflow,social,application
class WindowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'A'}],'context_dir':self.tmp.name,'max_per_group_day':None,'max_total_day':None,'interaction':{'enabled':True,'quiet_seconds':5,'max_wait_seconds':20}}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def add(self,mid,stamp):workflow.ingest(self.db,{'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':mid,'message':[{'type':'text','data':{'text':'消息'+str(mid)}}]},self.cfg,stamp)
 def test_continuous_messages_hit_deadline_and_new_arrivals_start_next_window(self):
  now=time.time();self.add(1,now-21);self.add(2,now-1)
  def reply(model,payload):
   self.assertTrue(payload['window']['forced_after_max_wait'])
   self.add(3,time.time()+.001)
   return {'action':'reply','text':'自然回复'},{}
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model',side_effect=reply):social.tick(self.db,self.cfg)
  self.assertEqual(self.db.execute('SELECT deadline_reply FROM deliveries').fetchone()[0],1)
  self.assertEqual(self.db.execute("SELECT count(*) FROM social_jobs WHERE status='pending'").fetchone()[0],1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM reaction_windows').fetchone()[0],0)
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':42}]):self.assertEqual(workflow.dispatch(self.db,self.cfg,{},time.time()+1),1)
 def test_continuous_messages_before_deadline_wait(self):
  now=time.time();self.add(1,now-18);self.add(2,now-1)
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model') as call:social.tick(self.db,self.cfg);call.assert_not_called()
 def test_awkward_or_difficult_replies_are_skipped_with_state_preserved(self):
  for quality in ('awkward','difficult',None):
   d=application.decision(application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'reply','quality':quality,'text':'硬凑一句','group_state':'当前群状态'}}})))
   self.assertEqual(d['action'],'wait');self.assertEqual(d['state_updates']['group_state'],'当前群状态')
