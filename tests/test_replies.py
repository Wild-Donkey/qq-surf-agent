"""reply_forms, emoticons, sticker_library, sticker_reactions regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
import tempfile
import time
import json
from unittest.mock import patch
from app import workflow
from app import social
from app import application
from app import replies
from app import emoticons
import hashlib
from app import vision
import base64
import io


class ReplyFormsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','context_dir':self.tmp.name,'groups':[{'id':1,'name':'A'}],'mode':'live','reply_delay_seconds':0}
  with self.db:self.db.execute('INSERT INTO group_activity VALUES (?,?)',('1',self.now))
  self.face=next(iter(emoticons.FACES));self.job={'source_group':'1','target_group':'1','event_key':'1:hello','trigger_text':'hello'}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_ordered_reply_uses_one_send_and_one_receipt(self):
  parts=[{'emoticon':{'kind':'face','id':self.face}},{'text':'绷不住了'},{'emoticon':{'kind':'face','id':self.face}}]
  d=application.decision(application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'reply','reply_parts':parts,'quality':'natural'}}})))
  cid=social.apply_decision(self.db,self.job,d,{},self.cfg,self.now)
  self.assertIsNotNone(cid)
  with patch('app.participants.humans_recent',return_value=True),patch('app.workflow.newer_context',return_value=False),patch('app.agent.onebot_call',side_effect=[{'user_id':99},{'message_id':42}]) as api:
   self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
  sent=[c.args[2] for c in api.call_args_list if c.args[1]=='send_group_msg']
  self.assertEqual(len(sent),1);self.assertEqual([p['type'] for p in sent[0]['message']],['face','text','face'])
  self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'sent')
 def test_reaction_sequence_can_have_no_text(self):
  d={'action':'reply','text':'','reply_parts':[{'emoticon':{'kind':'face','id':self.face}}]}
  self.assertIsNotNone(social.apply_decision(self.db,self.job,d,{},self.cfg,self.now))
 def test_invalid_or_excessive_sequence_is_not_queued(self):
  for parts in ([{'emoticon':{'kind':'sticker','id':'unknown'}}],[{'text':'x'}]*9):
   d={'action':'reply','text':'','reply_parts':parts}
   self.assertIsNone(social.apply_decision(self.db,self.job,d,{},self.cfg,self.now));self.assertEqual(d['action'],'wait')
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
 def test_parts_fingerprint_distinguishes_reactions_and_deduplicates_restarts(self):
  for _ in range(2):
   social.apply_decision(self.db,self.job,{'action':'reply','text':'','reply_parts':[{'emoticon':{'kind':'face','id':self.face}}]}, {},self.cfg,self.now)
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
 def test_recent_sticker_is_rejected(self):
  with patch('app.emoticons.safe_selection',return_value={'kind':'sticker','id':'x'}),patch('app.emoticons.recent_echo',return_value=True):
   with self.assertRaises(ValueError):replies.prepare(self.cfg,'1',[{'emoticon':{'kind':'sticker','id':'x'}}])


class EmoticonTests(unittest.TestCase):
 def test_native_face_normalizes_and_sends(self):
  identity=next(iter(emoticons.FACES));part={'type':'face','data':{'id':identity}}
  self.assertIn(emoticons.FACES[identity],workflow.normalize({'message':[part]})[0])
  with tempfile.TemporaryDirectory() as root:
   self.assertEqual(emoticons.segments(root,'1',{'kind':'face','id':identity}),[part])
   with self.assertRaises(ValueError):emoticons.segments(root,'1',{'kind':'face','id':'999999'})
 def test_cached_sticker_isolated_by_group(self):
  with tempfile.TemporaryDirectory() as root:
   with patch('app.vision.fetch',return_value=(b'jpeg-data',False)):
    clean,_=vision.prepare({'_vision':[{'reference':'a','url':'https://gchat.qpic.cn/a','cache_root':root,'group_id':'1','event_key':'1:2'}]})
   key=clean['vision_status']['attached'][0]['media_key'];emoticons.learn(root,'1','url','笑哭猫猫',key)
   item=emoticons.search(root,'1')[0]
   self.assertTrue(emoticons.segments(root,'1',{'kind':'sticker','id':item['id']})[0]['data']['file'].startswith('base64://'))
   with self.assertRaises(ValueError):emoticons.segments(root,'2',{'kind':'sticker','id':item['id']})
 def test_empty_text_face_reply_persists(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');identity=next(iter(emoticons.FACES))
   social.apply_decision(db,{'target_group':'1','event_key':'1:2'},{'action':'reply','text':'','emoticon':{'kind':'face','id':identity}}, {},{'context_dir':root},1)
   self.assertEqual(json.loads(db.execute('select emoticon from candidates').fetchone()[0])['id'],identity)
   db.close()
 def test_duplicate_event_repairs_missing_human_activity(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'bot_id':'99','groups':[{'id':'1','name':'test'}],'context_dir':root,'interaction':{'enabled':True},'human_presence':{'enabled':True,'known_humans':['3']}}
   db=workflow.connect(':memory:');event={'post_type':'message','message_type':'group','group_id':1,'message_id':7,'user_id':3,'time':100,'message':[{'type':'text','data':{'text':'你好'}}]}
   workflow.ingest(db,event,cfg,100)
   with db:db.execute('DELETE FROM speaker_events')
   self.assertEqual(workflow.ingest(db,event,cfg,101),'duplicate_event')
   import participants
   self.assertTrue(participants.humans_recent(db,'1',cfg,101));db.close()

 def test_invalid_optional_emoticon_preserves_text_reply(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');decision={'action':'reply','text':'正常文字','emoticon':'😂'}
   social.apply_decision(db,{'target_group':'1','event_key':'1:invalid'},decision,{}, {'context_dir':root},1)
   row=db.execute('SELECT text,emoticon FROM candidates').fetchone()
   self.assertEqual(row['text'],'正常文字');self.assertIsNone(row['emoticon']);self.assertTrue(decision['_emoticon_dropped']);db.close()
 def test_invalid_emoticon_only_reply_becomes_wait_without_requeue(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');decision={'action':'reply','text':'','emoticon':{'kind':'sticker','id':'nonexistent'}}
   social.apply_decision(db,{'target_group':'1','event_key':'1:invalid'},decision,{}, {'context_dir':root},1)
   self.assertEqual(decision['action'],'wait');self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],0);db.close()
 def test_safe_normalization_does_not_accept_arbitrary_assets(self):
  with tempfile.TemporaryDirectory() as root:
   identity=next(iter(emoticons.FACES))
   for value in (identity,int(identity),{'type':'face','id':identity}):self.assertEqual(emoticons.safe_selection(root,'1',value),{'kind':'face','id':identity})
   for value in ('file:///secret',True,[],{'kind':'face','id':'999999'},{'kind':'image','id':'https://example.com'}, {'kind':'face','id':True}):self.assertIsNone(emoticons.safe_selection(root,'1',value))


class StickerLibraryTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def asset(self,data=b'validated-image',extension='jpg'):
  key=hashlib.sha256(data).hexdigest()+'.'+extension;p=self.root/'media'/key;p.parent.mkdir(exist_ok=True);p.write_bytes(data);return key
 def test_auto_collection_stays_pending_until_described(self):
  key=self.asset();emoticons.observe_asset(self.root,'1',{'media_key':key,'sticker_hint':True})
  self.assertEqual(emoticons.catalog(self.root,'1')['stickers'],[])
  pending=emoticons.search(self.root,'1',include_unreviewed=True);self.assertEqual(len(pending),1)
  with self.assertRaises(ValueError):emoticons.segments(self.root,'1',{'kind':'sticker','id':pending[0]['id']})
  identity=emoticons.learn(self.root,'1','', '猫猫翻白眼',key,tags=['无语','嫌弃'])
  self.assertEqual(identity,pending[0]['id']);self.assertEqual(emoticons.search(self.root,'1','无语')[0]['id'],identity)
  self.assertEqual(emoticons.search(self.root,'2','无语'),[])
 def test_non_stickers_are_not_suggested_and_duplicate_assets_merge(self):
  key=self.asset();emoticons.observe_asset(self.root,'1',{'media_key':key})
  emoticons.learn(self.root,'1','signed-url-a','聊天截图',key,is_sticker=False)
  emoticons.learn(self.root,'1','signed-url-b','聊天截图',key,is_sticker=False)
  db=emoticons.connect(self.root);self.assertEqual(db.execute('SELECT count(*) FROM stickers').fetchone()[0],1);db.close()
  self.assertEqual(emoticons.catalog(self.root,'1')['stickers'],[])
 def test_original_animation_is_sent_instead_of_preview(self):
  preview=self.asset();raw=b'original-animated-gif';original=self.asset(raw,'gif')
  identity=emoticons.learn(self.root,'1','','猫猫蹦跳',preview,original_key=original)
  sent=emoticons.segments(self.root,'1',{'kind':'sticker','id':identity})[0]['data']['file']
  self.assertEqual(base64.b64decode(sent.removeprefix('base64://')),raw)
 def test_real_gif_survives_download_cache_and_offline_preview(self):
  from PIL import Image
  out=io.BytesIO();Image.new('RGB',(20,20),'red').save(out,format='GIF',save_all=True,append_images=[Image.new('RGB',(20,20),'blue')],duration=100,loop=0);raw=out.getvalue()
  item={'reference':'a','url':'https://gchat.qpic.cn/a','cache_root':str(self.root),'group_id':'1','event_key':'1:2','metadata':{'_sticker_hint':True}}
  with patch('app.vision.fetch',side_effect=lambda url:vision.decode_image(raw)):
   clean,images=vision.prepare({'_vision':[item]})
  meta=clean['vision_status']['attached'][0]
  self.assertTrue(meta['animated_first_frame']);self.assertTrue(meta['original_key'].endswith('.gif'));self.assertEqual((self.root/'media'/meta['original_key']).read_bytes(),raw)
  with patch('app.vision.resolve',side_effect=AssertionError('must use cache')):
   cached,_=vision.prepare({'_vision':[dict(item)]})
  self.assertEqual(cached['vision_status']['attached'][0]['original_key'],meta['original_key'])
  identity=emoticons.learn(self.root,'1','','跳舞',meta['media_key'],original_key=meta['original_key'])
  preview=emoticons.preview(self.root,'1',identity)
  with patch('app.vision.resolve',side_effect=AssertionError('offline preview')):
   viewed,_=vision.prepare({'_vision':[preview]})
  self.assertEqual(viewed['vision_status']['attached'][0]['original_key'],meta['original_key'])
 def test_search_preview_review_and_send_via_application(self):
  key=self.asset();emoticons.observe_asset(self.root,'1',{'media_key':key})
  cfg={'context_dir':str(self.root)};payload={};db=workflow.connect(':memory:')
  results=application.read(db,cfg,'1',payload,{'name':'search_emoticons','arguments':{'include_unreviewed':True}});identity=results['stickers'][0]['id']
  result=application.read(db,cfg,'1',payload,{'name':'view_emoticon','arguments':{'id':identity}})
  with patch('app.vision.resolve',side_effect=AssertionError('offline')):clean,_=vision.prepare(payload)
  payload['_attached_images']=clean['vision_status']['attached']
  self.assertEqual(payload['_attached_images'][0]['sticker_id'],identity)
  application.update_states(cfg,'1',payload,{'images':[{'reference':result['reference'],'description':'猫猫无语','is_sticker':True,'tags':['无语']}]},db)
  self.assertEqual(emoticons.search(self.root,'1','无语')[0]['id'],identity)
  self.assertTrue(emoticons.safe_selection(self.root,'1',{'kind':'sticker','id':identity}))
  self.assertEqual(emoticons.safe_selection(self.root,'1',identity),{'kind':'sticker','id':identity})
  self.assertIsNone(emoticons.safe_selection(self.root,'2',identity))
  with self.assertRaises(ValueError):application.read(db,cfg,'2',{}, {'name':'view_emoticon','arguments':{'id':identity}})
  emoticons.mark_used(self.root,'1',{'kind':'sticker','id':identity});self.assertEqual(emoticons.search(self.root,'1','无语')[0]['uses'],1);db.close()
 def test_mface_is_queued_as_real_image(self):
  db=workflow.connect(':memory:');cfg={'groups':[{'id':'1'}],'interaction':{'enabled':True}}
  social.enqueue(db,{'group_id':1,'user_id':2,'message_id':3,'message':[{'type':'mface','data':{'url':'https://gchat.qpic.cn/mface'}}]},cfg,100)
  row=db.execute('SELECT trigger_images,trigger_media FROM social_jobs').fetchone()
  self.assertEqual(row['trigger_images'],1);self.assertTrue(json.loads(row['trigger_media'])[0]['_sticker_hint']);db.close()

 def test_same_first_frame_different_animations_remain_distinct(self):
  preview=self.asset();one=self.asset(b'gif-one','gif');two=self.asset(b'gif-two','gif')
  first=emoticons.learn(self.root,'1','','动画1',preview,original_key=one)
  second=emoticons.learn(self.root,'1','','动画2',preview,original_key=two)
  self.assertNotEqual(first,second);self.assertEqual(len(emoticons.catalog(self.root,'1')['stickers']),2)

 def test_unattached_image_annotation_cannot_block_or_pollute_state(self):
  cfg={'context_dir':str(self.root)};payload={}
  application.update_states(cfg,'1',payload,{'group_state':'有效群状态','images':[{'reference':'invented','description':'未曾看到的图片','is_sticker':True}]})
  self.assertIn('有效群状态',application.group_state(cfg,'1'))
  self.assertEqual(payload['_rejected_image_annotations'],1)
  self.assertEqual(emoticons.catalog(self.root,'1')['stickers'],[])

 def test_invalid_auxiliary_memory_returns_feedback_without_aborting(self):
  cfg={'context_dir':str(self.root)};db=workflow.connect(':memory:')
  result=application.apply_worker_states(cfg,'1',{}, {'members':[{'user_id':'not-in-context','state':'bad'}]},db)
  self.assertFalse(result['saved']);self.assertEqual(result['error'],'member_outside_context')
  self.assertFalse((self.root/'people/not-in-context/people.md').exists())
  decision={'action':'reply','text':'仍然可以自然回复'}
  social.apply_decision(db,{'target_group':'1','event_key':'1:test'},decision,{},cfg,1)
  self.assertEqual(db.execute('SELECT text FROM candidates').fetchone()[0],decision['text']);db.close()

 def _dispatch_cfg(self):
  return {'bot_id':'99','mode':'live','groups':[{'id':'1','name':'A'}],'context_dir':str(self.root),
          'max_per_group_day':None,'max_total_day':None,'interaction':{'enabled':False}}
 def _queued_delivery(self,db,identity,text):
  cfg=self._dispatch_cfg()
  workflow.ingest(db,{'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':5,
                      'message':[{'type':'text','data':{'text':'就这？'}}]},cfg,99)
  queue_cfg={'context_dir':str(self.root),'groups':[{'id':'1'}],'interaction':{'enabled':True}}
  decision={'action':'reply','text':text,'emoticon':{'kind':'sticker','id':identity}}
  social.apply_decision(db,{'target_group':'1','event_key':'1:5','source_group':'1'},decision,{},queue_cfg,100)
 def test_dispatch_sends_sticker_on_its_own_bubble(self):
  key=self.asset();identity=emoticons.learn(self.root,'1','','猫猫嘲讽',key,tags=['嘲讽'])
  db=workflow.connect(':memory:');cfg=self._dispatch_cfg()
  self._queued_delivery(db,identity,'你这操作离谱')
  sent=[]
  def call(_,action,payload=None):
   if action=='get_login_info':return {'user_id':99}
   if action=='send_group_msg':sent.append(payload['message']);return {'message_id':42}
   raise AssertionError(action)
  with patch('app.agent.onebot_call',side_effect=call),patch('app.participants.humans_recent',return_value=True):
   self.assertEqual(workflow.dispatch(db,cfg,{},101),1)
  self.assertEqual(len(sent),2)
  self.assertEqual([p['type'] for p in sent[0]],['text'])
  self.assertEqual(sent[0][0]['data']['text'],'你这操作离谱')
  self.assertEqual([p['type'] for p in sent[1]],['image'])
  self.assertTrue(sent[1][0]['data']['file'].startswith('base64://'))
  db.close()
 def test_dispatch_allows_pure_sticker_reply(self):
  key=self.asset();identity=emoticons.learn(self.root,'1','','猫猫鄙视',key,tags=['鄙视'])
  db=workflow.connect(':memory:');cfg=self._dispatch_cfg()
  self._queued_delivery(db,identity,'')
  sent=[]
  def call(_,action,payload=None):
   if action=='get_login_info':return {'user_id':99}
   if action=='send_group_msg':sent.append(payload['message']);return {'message_id':43}
   raise AssertionError(action)
  with patch('app.agent.onebot_call',side_effect=call),patch('app.participants.humans_recent',return_value=True):
   self.assertEqual(workflow.dispatch(db,cfg,{},101),1)
  self.assertEqual(len(sent),1)
  self.assertEqual([p['type'] for p in sent[0]],['image'])
  self.assertEqual(db.execute("SELECT status FROM deliveries").fetchone()[0],'sent')
  self.assertEqual(db.execute("SELECT count(*) FROM events WHERE text LIKE '%[大表情]%'").fetchone()[0],1)
  db.close()


class StickerReactionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=time.time();self.db=workflow.connect(':memory:');self.cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'群'}],'context_dir':str(self.root)}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def asset(self,word):
  raw=word.encode();key=hashlib.sha256(raw).hexdigest()+'.jpg';(self.root/'media').mkdir(exist_ok=True);(self.root/'media'/key).write_bytes(raw)
  identity=emoticons.learn(self.root,'1','',word,key,tags=[word]);return identity,key
 def test_recommends_contextual_old_sticker_over_new_irrelevant_asset(self):
  old,_=self.asset('无语');new,_=self.asset('开心')
  self.assertEqual(emoticons.reactions(self.root,'1','这也太无语了')[0]['id'],old)
 def test_recent_received_is_excluded_and_repeat_observation_does_not_refresh_time(self):
  identity,key=self.asset('疑惑')
  emoticons.observe_asset(self.root,'1',{'media_key':key,'event_key':'1:1','received_at':self.now-100})
  emoticons.observe_asset(self.root,'1',{'media_key':key,'event_key':'1:1','received_at':self.now})
  self.assertFalse(emoticons.recent_echo(self.root,'1',identity,self.now))
  emoticons.observe_asset(self.root,'1',{'media_key':key,'event_key':'1:2','received_at':self.now})
  self.assertTrue(emoticons.recent_echo(self.root,'1',identity,self.now));self.assertEqual(emoticons.reactions(self.root,'1','疑惑'),[])
 def test_fresh_echo_is_not_sent_but_old_sticker_can_be_entire_reply(self):
  identity,key=self.asset('无语')
  emoticons.observe_asset(self.root,'1',{'media_key':key,'event_key':'1:1','received_at':self.now})
  d={'action':'reply','text':'','emoticon':{'kind':'sticker','id':identity}}
  social.apply_decision(self.db,{'target_group':'1','event_key':'1:fresh'},d,{},self.cfg,self.now)
  self.assertEqual(d['action'],'wait');self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
  with patch('app.emoticons.time.time',return_value=self.now+100):
   d={'action':'reply','text':'','emoticon':{'kind':'sticker','id':identity}}
   social.apply_decision(self.db,{'target_group':'1','event_key':'1:later'},d,{},self.cfg,self.now+100)
  with self.db:self.db.execute('INSERT INTO group_activity VALUES (?,?)',('1',self.now))
  with patch('app.agent.onebot_call',side_effect=[{'user_id':99},{'message_id':123}]):self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+101),1)
  self.assertEqual(self.db.execute('SELECT text FROM candidates').fetchone()[0],'')
  self.assertEqual(emoticons.reactions(self.root,'1','无语'),[])
  with patch('app.emoticons.time.time',return_value=self.now+300):self.assertEqual(emoticons.reactions(self.root,'1','无语')[0]['id'],identity)


if __name__ == '__main__':
    unittest.main()
