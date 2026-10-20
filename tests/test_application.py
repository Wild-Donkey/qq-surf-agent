import unittest,json,tempfile,time
from pathlib import Path
from unittest.mock import patch
import application,workflow,participants,social
class ApplicationTests(unittest.TestCase):
 def test_worker_only_calls_exposed_api(self):
  self.assertEqual(social.validate_decision('{"call":{"name":"reply","arguments":{"text":"好"}}}')['action'],'reply')
  for raw in [{'action':'reply','text':'hi','speakers':[]},{'call':{'name':'set_member','arguments':{}}},{'call':{'name':'reply','arguments':{'text':'hi','target_group':'999'}}},{'call':{'name':'wait','arguments':{}},'group_state':'overwrite'}]:
   with self.assertRaises(ValueError):application.decode(json.dumps(raw))
 def test_application_marks_target_not_controller(self):
  db=workflow.connect(':memory:');cfg={'bot_id':'99','human_presence':{'known_humans':['7']}}
  events=[{'user_id':'7','message_id':1,'time':1000,'group_id':'1','message':[{'type':'text','data':{'text':'真得给你清理上下文了'}},{'type':'at','data':{'qq':'9'}}]}, {'user_id':'9','message_id':2,'time':1001,'group_id':'1','message':[{'type':'text','data':{'text':'别清别清'}}]}]
  for e in events:
   with db:participants.observe(db,e,cfg,workflow.normalize(e)[0],e['time'])
  self.assertEqual(db.execute("SELECT kind FROM speaker_profiles WHERE user_id='9'").fetchone()[0],'bot')
  self.assertEqual(db.execute("SELECT kind FROM speaker_profiles WHERE user_id='7'").fetchone()[0],'human')
  self.assertEqual(db.execute("SELECT source FROM speaker_profiles WHERE user_id='9'").fetchone()[0],'app_rules')
  db.close()
 def test_accusation_without_target_acknowledgement_does_not_mark_bot(self):
  db=workflow.connect(':memory:');cfg={'bot_id':'99'}
  e={'user_id':'7','message_id':1,'time':1000,'group_id':'1','message':[{'type':'at','data':{'qq':'9'}}]}
  with db:participants.observe(db,e,cfg,'清理上下文',1000)
  self.assertIsNone(db.execute("SELECT kind FROM speaker_profiles WHERE user_id='9'").fetchone());db.close()
 def test_member_api_is_context_scoped(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:')
   with self.assertRaises(ValueError):application.read(db,{'context_dir':root},'1',{'member_profiles':[]},{'name':'get_member','arguments':{'user_id':'999'}})
   db.close()
 def test_states_persist_and_next_worker_gets_member_markdown(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root};payload={'member_profiles':[{'id':'9','current_judgment':{'kind':'bot'}}]}
   application.update_states(cfg,'1',payload,{'group_state':'群里称呼Luna，不要再续奶茶旧梗','members':[{'user_id':'9','state':'叫鲸鲸，明确表示不想被反复抓出来营业'}]})
   self.assertIn('不想被反复',application.member_context(cfg,'1',payload)[0]['markdown'])
   self.assertIn('不要再续',application.group_state(cfg,'1'))
   self.assertNotIn('不想被反复',application.member_context(cfg,'2',payload)[0]['markdown'])
   self.assertTrue((Path(root)/'people/9/people.md').exists())
 def test_invalid_member_in_bulk_request_writes_nothing(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root};payload={'member_profiles':[{'id':'9','current_judgment':{}}]}
   with self.assertRaises(ValueError):application.update_states(cfg,'1',payload,{'group_state':'bad','members':[{'user_id':'9','state':'first'},{'user_id':'../../secret','state':'bad'}]})
   self.assertFalse((Path(root)/'people/9/people.md').exists())
 def test_finish_turn_carries_state_via_api_only(self):
  d=application.decision(application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'wait','group_state':'正在聊bot','members':[]}}})))
  self.assertEqual(d['action'],'wait');self.assertEqual(d['state_updates']['group_state'],'正在聊bot')
