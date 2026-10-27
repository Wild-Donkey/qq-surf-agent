import concurrent.futures,json,tempfile,time,unittest
from unittest.mock import patch
from pathlib import Path
from app import speech, private_chat, workflow

class SpeechTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':99,'groups':[],'context_dir':self.tmp.name,'private_chat':{'enabled':True}}
 def tearDown(self):self.db.close();self.tmp.cleanup();speech._PENDING.clear()
 def add(self):
  private_chat.ingest(self.db,{'post_type':'message','message_type':'private','sub_type':'friend','user_id':7,'message_id':8,'time':self.now,'message':[{'type':'record','data':{'file':'voice-id','url':'https://unused.invalid'}}]},self.cfg,self.now)
 def test_voice_is_persisted_and_transcript_added_once(self):
  self.add();row=self.db.execute('SELECT * FROM private_messages').fetchone()
  self.assertEqual(json.loads(row['media'])[0]['_type'],'record')
  future=concurrent.futures.Future();speech._PENDING[('7','8',0)]=future
  self.assertFalse(speech.prepare(self.db,[row],{},self.cfg))
  future.set_result('你好，能听到吗')
  self.assertTrue(speech.prepare(self.db,[row],{},self.cfg))
  row=self.db.execute('SELECT * FROM private_messages').fetchone()
  self.assertIn('你好，能听到吗',row['text'])
  self.assertTrue(speech.prepare(self.db,[row],{},self.cfg))
  self.assertEqual(row['text'].count('你好，能听到吗'),1)
 def test_failed_voice_is_explicit_and_not_hallucinated(self):
  self.add();row=self.db.execute('SELECT * FROM private_messages').fetchone()
  future=concurrent.futures.Future();future.set_exception(ValueError('decode'))
  speech._PENDING[('7','8',0)]=future
  self.assertTrue(speech.prepare(self.db,[row],{},self.cfg))
  self.assertIn('不要猜测内容',self.db.execute('SELECT text FROM private_messages').fetchone()[0])
 def test_does_not_open_paths_from_message(self):
  with patch('app.speech.agent.onebot_call',return_value={'file':'/etc/passwd','base64':''}):
   with self.assertRaises(ValueError):speech.recognize({}, {'file':'voice-id'},self.tmp.name)
