import json,tempfile,time,unittest
from unittest.mock import patch
from pathlib import Path
from app import request_queue as rq, workflow, social, application
from app.sessions import SessionPool
from app.memory import MarkdownMemory
class RequestQueueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','groups':[{'id':'1','name':'来源群'},{'id':'2','name':'棺材房'}],'context_dir':self.tmp.name,'mode':'live','interaction':{'enabled':True,'quiet_seconds':0}}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def add(self,mid,text,stamp=None):
  stamp=stamp or self.now-10
  workflow.ingest(self.db,{'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':mid,'time':stamp,'message':[{'type':'text','data':{'text':text}}]},self.cfg,stamp)
 def test_request_persists_past_many_unrelated_messages_and_restart(self):
  self.add(1,'搬到棺材房给大伙看看')
  for mid in range(2,25):self.add(mid,'普通闲聊',self.now-5)
  with self.db:self.db.execute("UPDATE social_jobs SET status='decided'")
  rq.sync(self.db,self.cfg,self.now)
  task=rq.pending(self.db,'1')[0]
  self.assertEqual(task['text'],'搬到棺材房给大伙看看');self.assertEqual(task['target_group'],'2')
  self.assertEqual(self.db.execute("SELECT status FROM social_jobs WHERE event_key='1:1' AND target_group='1'").fetchone()[0],'pending')
 def test_unrelated_reply_cannot_ack_forward_request(self):
  self.add(1,'搬到棺材房给大伙看看');requests=rq.pending(self.db,'1')
  d={'action':'reply','text':'接别的话茬','request_ids':['1:1'],'request_outcome':'completed'}
  self.assertEqual(rq.accepts(self.db,'1',d,requests),[])
  d.update(text='目标群现在不活跃，先不搬',request_outcome='declined')
  self.assertEqual(rq.accepts(self.db,'1',d,requests),['1:1'])
 def test_reply_request_only_done_after_actual_send_despite_new_messages(self):
  self.add(1,'你是谁')
  args={'action':'reply','text':'我是Luna','quality':'natural','request_ids':['1:1'],'request_outcome':'completed'}
  d=social.validate_decision(json.dumps({'call':{'name':'finish_turn','arguments':args}}));ids=rq.accepts(self.db,'1',d,rq.pending(self.db,'1'))
  delivery=social.apply_decision(self.db,{'target_group':'1','event_key':'1:1'},d,{},self.cfg,self.now)
  rq.bind(self.db,'1',ids,d,delivery,self.now)
  self.assertEqual(self.db.execute('SELECT status FROM user_requests').fetchone()[0],'queued')
  self.add(2,'无关新消息',self.now+.001)
  with patch('app.agent.onebot_call',side_effect=[{'user_id':99},{'message_id':88}]):self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
  rq.sync(self.db,self.cfg,self.now+2);self.assertEqual(self.db.execute('SELECT status FROM user_requests').fetchone()[0],'done')
 def test_cancellation_stops_queued_action(self):
  self.add(1,'你是谁');d={'action':'reply','text':'我是Luna','request_outcome':'completed'}
  delivery=social.apply_decision(self.db,{'target_group':'1','event_key':'1:1'},d,{},self.cfg,self.now)
  rq.bind(self.db,'1',['1:1'],d,delivery,self.now);self.add(2,'取消请求',self.now+1)
  self.assertEqual(self.db.execute('SELECT status FROM user_requests').fetchone()[0],'cancelled')
  self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'cancelled')
 def test_busy_turn_cannot_silently_drop_request(self):
  self.add(1,'搬到棺材房给大伙看看')
  with patch('app.social.load_model',return_value={'enabled':True,'api_key':'test'}),patch('app.social.call_model',return_value=({'action':'reply','text':'鱼挺好吃'},{})):
   social.tick(self.db,self.cfg)
  self.assertEqual(len(rq.pending(self.db,'1')),1);self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
 def test_active_checkpoint_preserves_session_cache(self):
  pool=SessionPool();memory=MarkdownMemory(self.tmp.name)
  self.add(1,'普通闲聊');pool.sync(self.db,self.cfg,self.now)
  session=pool.groups['1'];session.cached_memory['keep']={'summary':'正在做的事'}
  pool.flush_active(memory)
  self.assertIn('keep',session.cached_memory);self.assertTrue(session.turns);memory.close()

 def test_disk_restart_keeps_unresolved_requests(self):
  path=Path(self.tmp.name)/'queue.sqlite3';db=workflow.connect(path)
  event={'group_id':1,'user_id':7,'message_id':9,'message':[]}
  with db:rq.observe(db,event,self.cfg,'搬到棺材房给大伙看看',self.now)
  db.close();db=workflow.connect(path)
  self.assertEqual(rq.pending(db,'1')[0]['event_key'],'1:9');db.close()
 def test_discussing_an_unrelated_bot_is_not_a_request(self):
  self.add(1,'这个bot开思考和没开一样')
  self.assertEqual(rq.pending(self.db,'1'),[])

 def test_explicit_forward_uses_pinned_original_context_not_new_topic(self):
  import merged_forward as mf
  self.add(1,'当时的铺垫',self.now-1100);self.add(2,'当时的包袱',self.now-1099)
  self.add(3,'搬到棺材房给大伙看看',self.now-1000);self.add(4,'后来已经换了话题',self.now-1)
  event={'post_type':'message','message_type':'group','group_id':2,'user_id':8,'message_id':20,'time':self.now-2,'message':[{'type':'text','data':{'text':'隔壁活跃'}}]}
  workflow.ingest(self.db,event,self.cfg,self.now-2)
  request=rq.pending(self.db,'1')[0];self.assertEqual([r['message_id'] for r in request['original_context']],['1','2'])
  bundles=mf.outgoing(self.db,self.cfg,'1',self.now,before=request['created']);self.assertTrue(bundles)
  bundle=bundles[0];decision={'action':'merged_forward','bundle_id':bundle['bundle_id'],'message_ids':['1','2'],'route_id':bundle['destination']['route_id'],'_outgoing_moments':bundles,'request_ids':['1:3'],'request_outcome':'completed'}
  delivery=social.apply_decision(self.db,{'target_group':'1'},decision,{},self.cfg,self.now)
  rq.bind(self.db,'1',['1:3'],decision,delivery,self.now)
  with patch('app.agent.onebot_call',side_effect=[{'user_id':99},{'message_id':77}]) as api:self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
  self.assertEqual([p['data']['id'] for p in api.call_args.args[2]['messages']],['1','2'])

 def test_mute_keeps_request_without_model_requeue(self):
  import moderation
  self.add(1,'你是谁')
  with self.db:self.db.execute("UPDATE social_jobs SET status='decided'")
  event={'post_type':'notice','notice_type':'group_ban','sub_type':'ban','group_id':1,'user_id':99,'operator_id':3,'time':self.now,'duration':500}
  moderation.ingest(self.db,event,self.cfg,self.now)
  rq.sync(self.db,self.cfg,self.now+1)
  self.assertEqual(self.db.execute('SELECT status FROM social_jobs').fetchone()[0],'decided')
  self.assertEqual(len(rq.pending(self.db,'1')),1)
  event.update(sub_type='lift_ban',duration=0,time=self.now+2)
  moderation.ingest(self.db,event,self.cfg,self.now+2)
  rq.sync(self.db,self.cfg,self.now+3)
  self.assertEqual(self.db.execute('SELECT status FROM social_jobs').fetchone()[0],'pending')
