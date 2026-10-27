"""conversation_context, conversation_refs, understanding, group_isolation regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import time
import unittest
from app import workflow
from app import social
from app.memory import MarkdownMemory
from app import archive
from app import conversation_refs
import json
from unittest.mock import patch
from app import understanding
from app import application


class ConversationContextTests(unittest.TestCase):
    def test_state_and_relevant_old_window_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=time.time();memory=MarkdownMemory(tmp)
            memory.remember_window('1',[{'event_key':'1:1','created':now-100}],
                                   '本群约定称呼Luna，正在讨论部署。','群友把bot称呼改成Luna，另有一只猫叫阿黄。')
            memory.remember_window('1',[{'event_key':'1:2','created':now}],
                                   '称呼Luna；当前在讨论晚饭。','现在在聊晚饭吃什么。')
            memory.close();memory=MarkdownMemory(tmp)
            self.assertIn('Luna',memory.conversation_state('1'))
            matches=memory.recall_episodes('1','那只猫阿黄叫什么')
            self.assertTrue(matches);self.assertIn('阿黄',matches[0]['summary'])
            self.assertEqual(memory.recall_episodes('2','阿黄'),[])
            self.assertIn('持续对话状态',(Path(tmp)/'groups/1/group.md').read_text())
            memory.close()
    def test_old_job_uses_latest_larger_context_with_speakers_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'bot_id':'99','groups':[{'id':'1','name':'A'}],'context_dir':tmp,'context_messages':100,
                 'context_char_budget':24000,'interaction':{'enabled':True}}
            db=workflow.connect(':memory:');now=time.time()
            for index in range(25):
                event={'post_type':'message','message_type':'group','group_id':1,'user_id':7+index%2,
                       'message_id':index+1,'sender':{'nickname':'群友'+str(index%2)},
                       'message':[{'type':'text','data':{'text':'历史消息'+str(index)}}]}
                workflow.ingest(db,event,cfg,now-50+index)
            event={'post_type':'message','message_type':'group','group_id':1,'user_id':8,'message_id':26,
                   'sender':{'nickname':'群友1'},'message':[{'type':'reply','data':{'id':23}},{'type':'text','data':{'text':'最新问题，接着上面说'}}]}
            workflow.ingest(db,event,cfg,now)
            job=db.execute('SELECT * FROM social_jobs ORDER BY created LIMIT 1').fetchone()
            memory=MarkdownMemory(tmp);payload,_=social.choose(db,cfg,memory,job)
            recent=payload['recent_target_messages']
            self.assertEqual(len(recent),26)
            self.assertEqual(recent[-1]['text'],'最新问题，接着上面说')
            self.assertEqual(recent[-1]['speaker'],'8')
            self.assertEqual(recent[-1]['reply_to']['text'],'历史消息22')
            memory.close();db.close()


class ReferenceTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=workflow.connect(':memory:');self.cfg={'context_dir':self.tmp.name,'bot_id':'99'}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def record(self,g,mid,user,name,text,stamp):
  disk=archive.Archive(self.root)
  try:disk.record({'group_id':g,'message_id':mid,'user_id':user,'sender':{'nickname':name},'time':stamp,'message':[{'type':'text','data':{'text':text}}]})
  finally:disk.close()
 def test_quoted_self_retains_author_and_retrieves_original_member(self):
  now=time.time();self.record(1,1,7,'Kyoko','邻居洗衣服洗到凌晨两点',now-10000);self.record(1,2,99,'Luna','有人洗衣服洗到凌晨两点',now-5000)
  memory=MarkdownMemory(self.root)
  try:view=social.message_view(self.db,memory,'1','那个人是谁',0,'8','2')
  finally:memory.close()
  self.assertEqual(view['reply_to']['speaker'],'99');self.assertEqual(view['reply_to']['nickname'],'Luna')
  payload={'unread_messages':[view],'recent_target_messages':[]};users=conversation_refs.enrich(self.cfg,'1',payload)
  self.assertIn('7',users);self.assertEqual(payload['reference_evidence'][0]['nickname'],'Kyoko');self.assertEqual(view['reply_to']['author_role'],'self')
 def test_quote_and_related_history_never_cross_groups(self):
  self.record(2,2,7,'foreign','洗衣服洗到凌晨两点',time.time())
  ref=conversation_refs.quote(self.db,self.root,'1','2');self.assertFalse(ref['available']);self.assertEqual(ref['speaker'],'unknown')
 def test_mentioned_and_quoted_members_are_selected_for_memory(self):
  self.record(1,3,7,'person','a quoted message with detail',time.time())
  view={'mentions':['8','all'],'reply_to':conversation_refs.quote(self.db,self.root,'1','3')}
  users=conversation_refs.enrich(self.cfg,'1',{'unread_messages':[view]})
  self.assertTrue({'7','8'}<=users)


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
  with patch('app.service_log.event') as log:receipt=understanding.save(self.cfg,'1',self.payload,decision['understanding'],self.db)
  self.assertTrue(receipt['saved']);self.assertIn('一只猫',log.call_args.args[1]);self.assertEqual(understanding.recent(self.cfg,'2'),[])
  path=Path(self.tmp.name)/'groups/1/requests'/(receipt['request_id']+'.md');self.assertIn('一只猫',path.read_text())
  # A later summary-only update retains already understood images.
  understanding.save(self.cfg,'1',self.payload,{'summary':'话题已收尾'},self.db)
  self.assertEqual(len(understanding.recent(self.cfg,'1')[0]['understanding']['images']),1)
 def test_unknown_image_is_not_logged_or_saved_as_observed(self):
  with patch('app.service_log.event') as log:receipt=understanding.save(self.cfg,'1',self.payload,{'images':[{'reference':'invented','description':'未经看过的图'}]},self.db)
  self.assertEqual(receipt['rejected_images'],1);self.assertEqual(receipt['understanding']['images'],[]);self.assertNotIn('未经看过',log.call_args.args[1])
 def test_independent_from_invalid_person_state(self):
  understanding.save(self.cfg,'1',self.payload,{'summary':'局面观察'},self.db)
  result=application.apply_worker_states(self.cfg,'1',self.payload,{'members':[{'user_id':'not-in-context','state':'bad'}]},self.db)
  self.assertFalse(result['saved']);self.assertEqual(understanding.recent(self.cfg,'1')[0]['understanding']['summary'],'局面观察')
 def test_tool_receipt_updates_next_model_context(self):
  result=application.read(self.db,self.cfg,'1',self.payload,{'name':'record_understanding','arguments':{'understanding':{'summary':'短暂安静'}}})
  self.assertTrue(result['saved']);self.assertIn('understanding_context',self.payload)
  self.assertEqual(self.payload['request_slot']['status'],'saved')


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
  with patch('app.social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('app.social.call_model',return_value=({'action':'reply','text':'本群回答'},{})) as call:social.tick(self.db,self.cfg)
  self.assertEqual([v['text'] for v in call.call_args.args[1]['unread_messages']],['本群问题'])
  self.assertEqual(self.db.execute("SELECT status FROM social_jobs WHERE event_key='2:2'").fetchone()[0],'cross_group_trigger_retired')
 def test_apply_and_dispatch_both_block_cross_group_plain_reply(self):
  d={'action':'reply','text':'不该隔空回复'}
  social.apply_decision(self.db,{'target_group':'1','source_group':'2','event_key':'2:2'},d,{},self.cfg,self.now)
  self.assertEqual(d['action'],'wait');self.assertEqual(self.db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)
  with self.db:
   self.db.execute("INSERT INTO candidates(fingerprint,source_group,text,images,created,status,kind) VALUES ('bad','2','跨群回复','[]',?,'approved','reply')",(self.now,))
   self.db.execute("INSERT INTO deliveries(candidate_id,target_group,fingerprint,status,created,updated) VALUES (1,'1','bad','approved',?,?)",(self.now,self.now))
  with patch('app.agent.onebot_call',return_value={'user_id':99}) as api:self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),0);self.assertEqual(api.call_count,1)
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


if __name__ == '__main__':
    unittest.main()
