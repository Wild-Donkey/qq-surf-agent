import hashlib,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import emoticons,social,workflow
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
  with patch('emoticons.time.time',return_value=self.now+100):
   d={'action':'reply','text':'','emoticon':{'kind':'sticker','id':identity}}
   social.apply_decision(self.db,{'target_group':'1','event_key':'1:later'},d,{},self.cfg,self.now+100)
  with self.db:self.db.execute('INSERT INTO group_activity VALUES (?,?)',('1',self.now))
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':123}]):self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+101),1)
  self.assertEqual(self.db.execute('SELECT text FROM candidates').fetchone()[0],'')
  self.assertEqual(emoticons.reactions(self.root,'1','无语'),[])
  with patch('emoticons.time.time',return_value=self.now+300):self.assertEqual(emoticons.reactions(self.root,'1','无语')[0]['id'],identity)
