"""merged_forward, image_sharing regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import agent
import application
import workflow
import social
import merged_forward as mf
import participants
import base64
import hashlib
import emoticons
import image_sharing
import urllib.error


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
   participants.observe(self.db,{'group_id':2,'user_id':77,'message_id':20,'time':self.now-20},self.cfg,'继续聊天',self.now-20)
   self.db.execute("UPDATE social_jobs SET status='decided'")
  workflow.ingest(self.db,{'post_type':'message','message_type':'group','group_id':1,'user_id':8,'message_id':4,'time':self.now-6,'message':[{'type':'text','data':{'text':'艾草'}}]},self.cfg,self.now-6)
  with self.db:self.db.execute("UPDATE social_jobs SET status='decided' WHERE target_group='2'")
  def model_call(model,payload):
   bundle=payload['outgoing_moments'][0]
   return social.validate_decision(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'merged_forward','bundle_id':bundle['bundle_id'],'route_id':bundle['destination']['route_id'],'message_ids':[m['message_id'] for m in bundle['messages']],'quality':'natural'}}})),{}
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model',side_effect=model_call):social.tick(self.db,self.cfg)
  row=self.db.execute("SELECT d.target_group,d.status,c.kind FROM deliveries d JOIN candidates c ON c.id=d.candidate_id").fetchone()
  self.assertIsNotNone(row);self.assertEqual(tuple(row),('2','approved','moment_forward'))
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


class ImageSharingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'来源'},{'id':'2','name':'目标'}],'context_dir':str(self.root),'interaction':{'enabled':True,'quiet_seconds':0},'max_per_group_day':None,'max_total_day':None,'max_shares_per_group_day':None,'max_per_tick':1}
  self.raw=b'original-image';key=hashlib.sha256(self.raw).hexdigest()+'.jpg';(self.root/'media').mkdir();(self.root/'media'/key).write_bytes(self.raw)
  self.image=emoticons.learn(self.root,'1','','新闻笑点图',key,is_sticker=False)
  for group in (1,2):workflow.ingest(self.db,{'post_type':'message','message_type':'group','group_id':group,'user_id':7+group,'message_id':group,'time':self.now-10,'message':[{'type':'text','data':{'text':'聊天'}}]},self.cfg,self.now-10)
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def selection(self):
  routes=image_sharing.destinations(self.db,self.cfg,'1',self.now)
  return {'action':'share_image','image_id':self.image,'route_id':routes[0]['route_id'],'_image_destinations':routes}
 def test_direct_image_preserves_bytes_and_sends_no_commentary(self):
  d=self.selection();social.apply_decision(self.db,{'target_group':'1','source_group':'1'},d,{},self.cfg,self.now)
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':90}]) as api:self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
  payload=api.call_args.args[2];self.assertEqual(payload['group_id'],2)
  self.assertEqual([p['type'] for p in payload['message']],['image'])
  self.assertEqual(base64.b64decode(payload['message'][0]['data']['file'].removeprefix('base64://')),self.raw)
 def test_scoped_asset_route_and_unreviewed_content_are_rejected(self):
  d=self.selection()
  with self.assertRaises(ValueError):image_sharing.approve(self.db,self.cfg,'2',d,self.now)
  with self.assertRaises(ValueError):image_sharing.approve(self.db,self.cfg,'1',dict(d,route_id='fake'),self.now)
  db=emoticons.connect(self.root)
  with db:db.execute('UPDATE stickers SET reviewed=0')
  db.close()
  with self.assertRaises(ValueError):image_sharing.approve(self.db,self.cfg,'1',d,self.now)
 def test_same_image_to_same_target_is_deduplicated(self):
  d=self.selection();one=image_sharing.approve(self.db,self.cfg,'1',d,self.now);two=image_sharing.approve(self.db,self.cfg,'1',d,self.now)
  self.assertEqual(one,two);self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
 def test_comment_and_share_are_separate_explicit_decisions(self):
  def model_call(model,payload):
   return {'action':'reply','text':'自然的本群评论','image_share':{'image_id':self.image,'route_id':payload['image_destinations'][0]['route_id'],'quality':'natural','reason':'这张新闻图的反差符合目标群趣味'}},{}
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model',side_effect=model_call):social.tick(self.db,self.cfg)
  self.assertEqual(self.db.execute("SELECT count(*) FROM candidates WHERE kind='reply'").fetchone()[0],1)
  self.assertEqual(self.db.execute("SELECT count(*) FROM candidates WHERE kind='image_share'").fetchone()[0],1)
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':91}]) as api:workflow.dispatch(self.db,self.cfg,{},self.now+1)
  self.assertEqual(api.call_args.args[2]['group_id'],1);self.assertEqual(api.call_args.args[2]['message'][0]['type'],'text')
 def test_uninteresting_image_does_not_auto_share(self):
  with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('social.call_model',return_value=({'action':'wait','text':''},{})):social.tick(self.db,self.cfg)
  self.assertEqual(self.db.execute("SELECT count(*) FROM candidates WHERE kind='image_share'").fetchone()[0],0)
 def test_interface_and_natural_quality_guard(self):
  for name in ('share_image','finish_turn'):
   args={'image_id':self.image,'route_id':'route','quality':'natural'}
   if name=='finish_turn':args['action']='share_image'
   d=social.validate_decision(json.dumps({'call':{'name':name,'arguments':args}}));self.assertEqual(d['action'],'share_image')
   args['quality']='awkward';d=social.validate_decision(json.dumps({'call':{'name':name,'arguments':args}}));self.assertEqual(d['action'],'wait')


class ForwardDispatchTests(unittest.TestCase):

    def test_forward_scope_and_common_send_limit(self):
        with tempfile.TemporaryDirectory() as root:
            db = workflow.connect(':memory:')
            now = time.time()
            cfg = {'bot_id': '99', 'mode': 'live', 'groups': [{'id': '1', 'name': 'A'}], 'context_dir': root, 'hold_seconds': 0, 'max_per_tick': 1, 'group_cooldown_seconds': 0, 'max_per_group_day': None, 'max_total_day': None}
            job = {'target_group': '1', 'source_group': '1', 'event_key': '1:1'}
            with db:
                db.execute('INSERT INTO group_activity VALUES (?,?)', ('1', now))
                for mid in ('1', '2'):
                    db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)', ('1', mid, 'f' + mid, 'content', '[]', now - 2))
            with self.assertRaises(ValueError):
                social.apply_decision(db, job, {'action': 'forward', 'message_id': 'other'}, {}, cfg, now)
            for mid in ('1', '2'):
                social.apply_decision(db, dict(job, event_key='1:' + mid), {'action': 'forward', 'message_id': mid, '_forward_message_ids': [mid], '_context_watermark': now, '_deadline_reply': True}, {}, cfg, now)
            with patch('agent.onebot_call', side_effect=[{'user_id': 99}, {'message_id': 123}]) as api:
                self.assertEqual(workflow.dispatch(db, cfg, {}, now + 1), 1)
                self.assertEqual(api.call_count, 2)
                self.assertEqual(api.call_args.args[1], 'send_group_forward_msg')
            self.assertEqual(db.execute("SELECT count(*) FROM deliveries WHERE status='sent'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE message_id='bot:123'").fetchone()[0], 1)
            db.close()


if __name__ == '__main__':
    unittest.main()
