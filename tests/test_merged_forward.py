import json,tempfile,time,unittest
from unittest.mock import patch
import agent,application,workflow,social,merged_forward as mf,participants
class MergedForwardTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'原群'},{'id':'2','name':'隔壁'}],'context_dir':self.tmp.name,'interaction':{'enabled':True},'hold_seconds':9999,'group_cooldown_seconds':0,'max_per_group_day':None,'max_total_day':None,'max_per_tick':1}
  for n in range(1,4):
   workflow.ingest(self.db,{'post_type':'message','message_type':'group','group_id':1,'user_id':7+n,'message_id':n,'time':self.now-10+n,'message':[{'type':'text','data':{'text':'艾草'}}]},self.cfg,self.now-10+n)
  with self.db:self.db.execute('INSERT INTO group_activity VALUES (?,?)',('2',self.now))
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def decision(self):
  bundles=mf.available(self.db,self.cfg,'2',self.now)
  return {'action':'merged_forward','bundle_id':bundles[0]['bundle_id'],'message_ids':['1','2','3'],'_forward_bundles':bundles,'_context_watermark':self.now,'_deadline_reply':True}
 def approve(self,d=None):social.apply_decision(self.db,{'target_group':'2'},d or self.decision(),{},self.cfg,self.now)
 def test_protocol_quality_and_finish(self):
  d=self.decision();args={k:d[k] for k in ('bundle_id','message_ids')};args['quality']='natural'
  for name in ('merged_forward','finish_turn'):
   args=dict(args,**({'action':'merged_forward'} if name=='finish_turn' else {}))
   result=application.decision(application.decode(json.dumps({'call':{'name':name,'arguments':args}})))
   self.assertEqual(result['action'],'merged_forward');self.assertEqual(result['message_ids'],['1','2','3'])
  args['quality']='awkward'
  self.assertEqual(application.decision(application.decode(json.dumps({'call':{'name':'finish_turn','arguments':args}})))['action'],'wait')
 def test_candidate_order_and_scope(self):
  d=self.decision();self.assertEqual([m['message_id'] for m in d['_forward_bundles'][0]['messages']],['1','2','3'])
  for ids in (['2','1'],['1','x'],['1'],['1','1']):
   with self.assertRaises(ValueError):self.approve(dict(d,message_ids=ids))
  with self.assertRaises(ValueError):self.approve(dict(d,bundle_id='invented'))
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
 def test_cross_group_send_preserves_nodes_and_immediate_delivery(self):
  self.approve()
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':888}]) as api:
   self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
   self.assertEqual(api.call_args.args[1],'send_group_forward_msg')
   self.assertEqual(api.call_args.args[2],{'group_id':2,'messages':[{'type':'node','data':{'id':x}} for x in ['1','2','3']]})
  self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'sent')
 def test_overlapping_bundles_are_not_resent_after_ambiguous_send(self):
  d=self.decision();self.approve(d)
  with patch('agent.onebot_call',side_effect=[{'user_id':99},TimeoutError()]):workflow.dispatch(self.db,self.cfg,{},self.now+1)
  self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'uncertain')
  self.approve(d);self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
  self.assertEqual(mf.available(self.db,self.cfg,'2',self.now),[])
 def test_privacy_and_group_optout(self):
  with self.db:self.db.execute("UPDATE events SET text='不要转发' WHERE message_id='1'")
  self.assertEqual(mf.available(self.db,self.cfg,'2',self.now),[])
  self.cfg['groups'][0]['allow_cross_group_forward']=False
  self.assertEqual(mf.available(self.db,self.cfg,'2',self.now),[])
 def test_disabled_target_and_human_gate(self):
  self.approve();self.cfg['human_presence']={'enabled':True}
  with patch('agent.onebot_call',return_value={'user_id':99}) as api:self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),0);self.assertEqual(api.call_count,1)
 def test_final_payload_revalidates_privacy_and_source(self):
  self.approve();row=self.db.execute('SELECT text FROM candidates').fetchone()
  self.assertEqual(len(mf.send_payload(self.db,self.cfg,'2','1',row[0])['messages']),3)
  with self.db:self.db.execute("UPDATE events SET text='手机：13812345678' WHERE message_id='1'")
  with self.assertRaises(ValueError):mf.send_payload(self.db,self.cfg,'2','1',row[0])
 def test_bridge_requires_message_receipt(self):
  cfg={'sender':{'url':'http://127.0.0.1:3000','token':'x'}}
  with patch('agent.request',return_value=json.dumps({'status':'ok','retcode':0,'data':{}})):
   with self.assertRaises(ValueError):agent.onebot_call(cfg,'send_group_forward_msg',{})
 def test_message_trigger_worker_receives_bundle_and_queues_card(self):
  with self.db:
   self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('2','20','target-fp','继续聊天','[]',self.now-20))
   self.db.execute("UPDATE social_jobs SET status='decided'")
  workflow.ingest(self.db,{'post_type':'message','message_type':'group','group_id':1,'user_id':8,'message_id':4,'time':self.now-6,'message':[{'type':'text','data':{'text':'艾草'}}]},self.cfg,self.now-6)
  with self.db:self.db.execute("UPDATE social_jobs SET status='decided' WHERE target_group='1'")
  def model_call(model,payload):
   bundle=payload['forward_bundles'][0]
   return social.validate_decision(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'merged_forward','bundle_id':bundle['bundle_id'],'message_ids':[m['message_id'] for m in bundle['messages']],'quality':'natural'}}})),{}
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model',side_effect=model_call):social.tick(self.db,self.cfg)
  row=self.db.execute("SELECT d.target_group,d.status,c.kind FROM deliveries d JOIN candidates c ON c.id=d.candidate_id").fetchone()
  self.assertIsNotNone(row);self.assertEqual(tuple(row),('2','approved','merged_forward'))
 def test_aged_source_and_optout_after_draft_are_rejected(self):
  self.approve();data=self.db.execute('SELECT text FROM candidates').fetchone()[0]
  with self.assertRaises(ValueError):mf.send_payload(self.db,self.cfg,'2','1',data,self.now+1000)
  self.cfg['groups'][1]['allow_cross_group_forward']=False
  with self.assertRaises(ValueError):mf.send_payload(self.db,self.cfg,'2','1',data)
 def outgoing_decision(self):
  event={'post_type':'message','message_type':'group','group_id':2,'user_id':77,'message_id':20,'time':self.now-2,'message':[{'type':'text','data':{'text':'隔壁在聊天'}}]}
  workflow.ingest(self.db,event,self.cfg,self.now-2)
  moments=mf.outgoing(self.db,self.cfg,'1',self.now)
  self.assertTrue(moments)
  bundle=moments[0]
  return {'action':'merged_forward','bundle_id':bundle['bundle_id'],'message_ids':['1','2','3'],'route_id':bundle['destination']['route_id'],'_outgoing_moments':moments}
 def test_source_worker_can_publish_to_active_neighbor(self):
  decision=self.outgoing_decision()
  social.apply_decision(self.db,{'target_group':'1'},decision,{},self.cfg,self.now)
  row=self.db.execute('SELECT d.target_group,c.kind FROM deliveries d JOIN candidates c ON c.id=d.candidate_id').fetchone()
  self.assertEqual(tuple(row),('2','moment_forward'))
  self.cfg['max_shares_per_group_day']=None
  with self.db:self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('2','newer','newer','隔壁继续聊天','[]',self.now+1))
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':888}]):self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+2),1)
 def test_outgoing_route_cannot_be_forged_or_sent_to_idle_group(self):
  decision=self.outgoing_decision()
  with self.assertRaises(ValueError):social.apply_decision(self.db,{'target_group':'1'},dict(decision,route_id='invented'),{},self.cfg,self.now)
  self.cfg['human_presence']={'enabled':True}
  with self.db:self.db.execute("DELETE FROM speaker_events WHERE group_id='2'")
  self.assertEqual(mf.outgoing(self.db,self.cfg,'1',self.now),[])
  mf.approve_outgoing(self.db,self.cfg,'1',decision,self.now)
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
 def test_normal_reply_does_not_consume_program_share_cooldown(self):
  decision=self.outgoing_decision()
  social.apply_decision(self.db,{'target_group':'2','event_key':'2:reply'},{'action':'reply','text':'普通接话'}, {},self.cfg,self.now-1)
  with self.db:self.db.execute("UPDATE deliveries SET status='sent',updated=?",(self.now-1,))
  social.apply_decision(self.db,{'target_group':'1'},decision,{},self.cfg,self.now)
  self.cfg['group_cooldown_seconds']=3600
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':889}]):self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
 def test_outgoing_interface_retains_route_selector(self):
  d=self.outgoing_decision()
  args={k:d[k] for k in ('action','bundle_id','message_ids','route_id')};args['quality']='natural'
  result=social.validate_decision(json.dumps({'call':{'name':'finish_turn','arguments':args}}))
  self.assertEqual(result['route_id'],d['route_id'])
 def test_program_keeps_quoted_messages_and_bot_punchline(self):
  with self.db:
   self.db.execute("UPDATE social_jobs SET trigger_reply='1' WHERE event_key='1:2'")
   self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('1','bot:99','bot-punchline','[自己] 包袱','[]',self.now-1))
  decision=self.outgoing_decision()
  candidate=decision['_outgoing_moments'][0]
  self.assertEqual([m['message_id'] for m in candidate['messages']],['1','2','3','99'])
  self.assertEqual(candidate['messages'][1]['reply_to'],'1')
