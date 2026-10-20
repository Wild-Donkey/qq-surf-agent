import base64,hashlib,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import workflow,social,emoticons,image_sharing,application
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
