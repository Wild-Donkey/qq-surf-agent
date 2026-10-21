import json,tempfile,time,unittest
from unittest.mock import patch
from pathlib import Path
import workflow,participants,replies,interactions,identities,social,application

class InteractionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'context_dir':self.tmp.name,'bot_id':'99','groups':[{'id':1,'name':'A'},{'id':2,'name':'B'}],'mode':'live','hold_seconds':0,'reply_delay_seconds':0,'max_per_tick':3,'group_cooldown_seconds':0,'max_per_group_day':None,'max_total_day':None,'max_shares_per_group_day':None,'interaction':{'enabled':True,'quiet_seconds':0},'private_chat':{'enabled':True}}
  self.event={'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':123,'time':self.now,'sender':{'nickname':'小甲'},'message':[{'type':'text','data':{'text':'hello'}}]}
  workflow.ingest(self.db,self.event,self.cfg,self.now)
  r=identities.Registry(self.tmp.name);r.alias('7','小甲');r.alias('8','隔壁');r.close()
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_at_and_quote_segments_and_cross_group_rejection(self):
  prepared=replies.prepare(self.cfg,'1',[{'reply_to':'123'},{'at':'小甲'},{'text':'想确认一下'}],db=self.db)
  self.assertEqual(replies.segments(self.tmp.name,'1',prepared),[{'type':'reply','data':{'id':'123'}},{'type':'at','data':{'qq':'7'}},{'type':'text','data':{'text':'想确认一下'}}])
  with self.assertRaises(ValueError):replies.prepare(self.cfg,'2',[{'reply_to':'123'},{'text':'no'}],db=self.db)
  with self.assertRaises(ValueError):replies.prepare(self.cfg,'1',[{'at':'隔壁'}],db=self.db)
  with self.assertRaises(ValueError):replies.prepare(self.cfg,'1',[{'at':'all'}],db=self.db)
 def test_group_poke_uses_queue_and_cooldown(self):
  job={'target_group':'1','source_group':'1','event_key':'1:123'}
  decision=application.decision(application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'poke','alias':'小甲','quality':'natural'}}})))
  ident=social.apply_decision(self.db,job,decision,{},self.cfg,self.now)
  self.assertIsNotNone(ident)
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{}]) as api:
   self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
  self.assertEqual(api.call_args.args[1:3],('group_poke',{'group_id':1,'user_id':7}))
  duplicate={'action':'poke','alias':'小甲'}
  self.assertIsNone(social.apply_decision(self.db,job,duplicate,{},self.cfg,self.now+2));self.assertEqual(duplicate['action'],'wait')
  self.assertEqual(self.db.execute('SELECT status FROM deliveries WHERE id=?',(ident,)).fetchone()[0],'sent')
 def test_receive_group_and_friend_poke_no_echo_loop(self):
  notice={'post_type':'notice','notice_type':'notify','sub_type':'poke','group_id':1,'user_id':7,'target_id':99,'time':self.now}
  self.assertEqual(workflow.ingest(self.db,notice,self.cfg,self.now),'context_only')
  self.assertEqual(workflow.ingest(self.db,notice,self.cfg,self.now),'duplicate_event')
  row=self.db.execute("SELECT trigger_text FROM social_jobs WHERE event_key LIKE '1:poke:%'").fetchone();self.assertIn('交互通知',row[0])
  self.assertEqual(workflow.ingest(self.db,dict(notice,user_id=99,target_id=7),self.cfg,self.now),'ignored_poke')
  friend={**notice,'group_id':None,'user_id':99,'sender_id':7}
  self.assertEqual(workflow.ingest(self.db,friend,self.cfg,self.now),'private_message')
  self.assertIn('交互通知',self.db.execute('SELECT text FROM private_messages WHERE user_id=?',('7',)).fetchone()[0])
 def test_quote_reply_goes_through_regular_delivery(self):
  job={'target_group':'1','source_group':'1','event_key':'1:123','trigger_text':'hello'}
  ident=social.apply_decision(self.db,job,{'action':'reply','text':'','reply_parts':[{'reply_to':'123'},{'at':'小甲'},{'text':'你指的是哪台？'}]}, {},self.cfg,self.now)
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':789}]) as api:workflow.dispatch(self.db,self.cfg,{},self.now+1)
  self.assertEqual(api.call_args.args[2]['message'][0]['type'],'reply')
  self.assertEqual(api.call_args.args[2]['message'][1]['type'],'at')
 def test_private_poke_is_bound_to_current_friend_and_not_repeated(self):
  import private_chat
  now=time.time()
  event={'post_type':'message','message_type':'private','sub_type':'friend','user_id':7,'message_id':'p1','message':[{'type':'text','data':{'text':'戳一下'}}]}
  private_chat.ingest(self.db,event,self.cfg,now-10)
  raw=json.dumps({'call':{'name':'finish_turn','arguments':{'action':'poke','quality':'natural'}}})
  with patch('social.load_model',return_value={'enabled':True,'api_key':'fake'}),patch('social.generate_text',return_value=(raw,{})),patch('agent.onebot_call',side_effect=[[{'user_id':7}],{'user_id':99},{}]) as api:
   private_chat.tick(self.db,self.cfg,{},now)
  self.assertEqual(api.call_args.args[1:3],('send_poke',{'user_id':7}))
  private_chat.ingest(self.db,dict(event,message_id='p2'),self.cfg,now+1)
  with patch('social.load_model',return_value={'enabled':True,'api_key':'fake'}),patch('social.generate_text',return_value=(raw,{})),patch('agent.onebot_call',return_value=[{'user_id':7}]) as api:
   private_chat.tick(self.db,self.cfg,{},now+10)
  self.assertEqual([call.args[1] for call in api.call_args_list],['get_friend_list'])

 def test_numeric_alias_cannot_redirect_stored_poke_recipient(self):
  registry=identities.Registry(self.tmp.name);registry.set('8','7','initial');registry.close()
  workflow.ingest(self.db,{**self.event,'user_id':8,'message_id':124},self.cfg,self.now)
  self.assertEqual(interactions.member(self.cfg,'1','7',self.db),'8')
  self.assertEqual(interactions.member(self.cfg,'1','7',self.db,raw_id=True),'7')
