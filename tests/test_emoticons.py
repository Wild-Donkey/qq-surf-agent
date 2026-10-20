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
