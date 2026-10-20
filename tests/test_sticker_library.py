import base64,hashlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import emoticons,vision,workflow,social,application
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
  with patch('vision.fetch',side_effect=lambda url:vision.decode_image(raw)):
   clean,images=vision.prepare({'_vision':[item]})
  meta=clean['vision_status']['attached'][0]
  self.assertTrue(meta['animated_first_frame']);self.assertTrue(meta['original_key'].endswith('.gif'));self.assertEqual((self.root/'media'/meta['original_key']).read_bytes(),raw)
  with patch('vision.resolve',side_effect=AssertionError('must use cache')):
   cached,_=vision.prepare({'_vision':[dict(item)]})
  self.assertEqual(cached['vision_status']['attached'][0]['original_key'],meta['original_key'])
  identity=emoticons.learn(self.root,'1','','跳舞',meta['media_key'],original_key=meta['original_key'])
  preview=emoticons.preview(self.root,'1',identity)
  with patch('vision.resolve',side_effect=AssertionError('offline preview')):
   viewed,_=vision.prepare({'_vision':[preview]})
  self.assertEqual(viewed['vision_status']['attached'][0]['original_key'],meta['original_key'])
 def test_search_preview_review_and_send_via_application(self):
  key=self.asset();emoticons.observe_asset(self.root,'1',{'media_key':key})
  cfg={'context_dir':str(self.root)};payload={};db=workflow.connect(':memory:')
  results=application.read(db,cfg,'1',payload,{'name':'search_emoticons','arguments':{'include_unreviewed':True}});identity=results['stickers'][0]['id']
  result=application.read(db,cfg,'1',payload,{'name':'view_emoticon','arguments':{'id':identity}})
  with patch('vision.resolve',side_effect=AssertionError('offline')):clean,_=vision.prepare(payload)
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
