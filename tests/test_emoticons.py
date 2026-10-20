import tempfile,unittest,json,hashlib
from pathlib import Path
from unittest.mock import patch
import emoticons,vision,workflow,social
class EmoticonTests(unittest.TestCase):
 def test_native_face_normalizes_and_sends(self):
  identity=next(iter(emoticons.FACES));part={'type':'face','data':{'id':identity}}
  self.assertIn(emoticons.FACES[identity],workflow.normalize({'message':[part]})[0])
  with tempfile.TemporaryDirectory() as root:
   self.assertEqual(emoticons.segments(root,'1',{'kind':'face','id':identity}),[part])
   with self.assertRaises(ValueError):emoticons.segments(root,'1',{'kind':'face','id':'999999'})
 def test_cached_sticker_isolated_by_group(self):
  with tempfile.TemporaryDirectory() as root:
   with patch('vision.fetch',return_value=(b'jpeg-data',False)):
    clean,_=vision.prepare({'_vision':[{'reference':'a','url':'https://gchat.qpic.cn/a','cache_root':root,'group_id':'1','event_key':'1:2'}]})
   key=clean['vision_status']['attached'][0]['media_key'];emoticons.learn(root,'1','url','笑哭猫猫',key)
   item=emoticons.catalog(root,'1')['stickers'][0]
   self.assertTrue(emoticons.segments(root,'1',{'kind':'sticker','id':item['id']})[0]['data']['file'].startswith('base64://'))
   with self.assertRaises(ValueError):emoticons.segments(root,'2',{'kind':'sticker','id':item['id']})
 def test_empty_text_face_reply_persists(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');identity=next(iter(emoticons.FACES))
   social.apply_decision(db,{'target_group':'1','event_key':'1:2'},{'action':'reply','text':'','emoticon':{'kind':'face','id':identity}}, {},{'context_dir':root},1)
   self.assertEqual(json.loads(db.execute('select emoticon from candidates').fetchone()[0])['id'],identity)
   db.close()
