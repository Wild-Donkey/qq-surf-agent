import json,tempfile,time,unittest
from unittest.mock import patch
import workflow,social,application
from memory import MarkdownMemory
class GroupIsolationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'甲群'},{'id':'2','name':'乙群'}],'context_dir':self.tmp.name,'interaction':{'enabled':True,'quiet_seconds':0}}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def add(self,group,mid,text):
  workflow.ingest(self.db,{'post_type':'message','message_type':'group','group_id':group,'user_id':7+group,'message_id':mid,'time':self.now-10,'message':[{'type':'text','data':{'text':text}}]},self.cfg,self.now-10)
 def test_even_active_groups_do_not_receive_mirrored_jobs(self):
  self.add(1,1,'甲群话题');self.add(2,2,'乙群话题')
  self.assertEqual(self.db.execute('SELECT count(*) FROM social_jobs WHERE source_group!=target_group').fetchone()[0],0)
 def test_legacy_mirror_cannot_enter_local_batch_or_generate_reply(self):
  self.add(1,1,'本群问题')
  with self.db:self.db.execute("INSERT INTO social_jobs(event_key,target_group,source_group,user_id,created,status,trigger_text) VALUES ('2:2','1','2','9',?,'pending','隔壁问题')",(self.now-11,))
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model',return_value=({'action':'reply','text':'本群回答'},{})) as call:social.tick(self.db,self.cfg)
  self.assertEqual([v['text'] for v in call.call_args.args[1]['unread_messages']],['本群问题'])
  self.assertEqual(self.db.execute("SELECT status FROM social_jobs WHERE event_key='2:2'").fetchone()[0],'cross_group_trigger_retired')
 def test_apply_and_dispatch_both_block_cross_group_plain_reply(self):
  d={'action':'reply','text':'不该隔空回复'}
  social.apply_decision(self.db,{'target_group':'1','source_group':'2','event_key':'2:2'},d,{},self.cfg,self.now)
  self.assertEqual(d['action'],'wait');self.assertEqual(self.db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)
  with self.db:
   self.db.execute("INSERT INTO candidates(fingerprint,source_group,text,images,created,status,kind) VALUES ('bad','2','跨群回复','[]',?,'approved','reply')",(self.now,))
   self.db.execute("INSERT INTO deliveries(candidate_id,target_group,fingerprint,status,created,updated) VALUES (1,'1','bad','approved',?,?)",(self.now,self.now))
  with patch('agent.onebot_call',return_value={'user_id':99}) as api:self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),0);self.assertEqual(api.call_count,1)
  self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'blocked_cross_group_reply')
 def test_foreign_material_is_not_in_reply_payload(self):
  self.add(1,1,'甲群问题');self.add(2,2,'乙群独有的句子')
  with self.db:self.db.execute("INSERT INTO candidates(fingerprint,source_group,text,images,created,status,kind) VALUES ('foreign','2','乙群独有的句子','[]',?,'approved','reply')",(self.now,))
  job=self.db.execute("SELECT * FROM social_jobs WHERE target_group='1'").fetchone();memory=MarkdownMemory(self.tmp.name)
  payload,_=social.choose(self.db,self.cfg,memory,job);memory.close()
  self.assertEqual(payload['candidates'],[]);self.assertEqual(payload['forward_bundles'],[])
  self.assertEqual(payload['trigger_event']['text'],'甲群问题')
  self.assertNotIn('乙群独有的句子',json.dumps(payload,ensure_ascii=False))
 def test_foreign_batch_cannot_be_saved_as_local_episode(self):
  memory=MarkdownMemory(self.tmp.name)
  with self.assertRaises(ValueError):application.settle_memory(memory,'1',[{'source_group':'2','event_key':'2:1','created':self.now}],[{'speaker':'9','text':'隔壁对话'}])
  self.assertEqual(memory.db.execute('SELECT count(*) FROM conversation_episodes').fetchone()[0],0);memory.close()
 def test_confirmed_wrong_replies_are_not_reintroduced_from_history(self):
  self.add(1,1,'本群正常问题')
  with self.db:
   self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('1','bot:88','wrong','[自己] 历史串群错误','[]',self.now-5))
   self.db.execute('INSERT INTO metrics VALUES (?,?)',('cross_group_bad_replies:1',json.dumps(['88'])))
  job=self.db.execute("SELECT * FROM social_jobs WHERE target_group='1'").fetchone();memory=MarkdownMemory(self.tmp.name)
  payload,_=social.choose(self.db,self.cfg,memory,job);memory.close()
  self.assertEqual([v['message_id'] for v in payload['recent_target_messages']],['1'])
