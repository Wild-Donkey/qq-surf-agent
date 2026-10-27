import unittest,json,tempfile,time
from pathlib import Path
from unittest.mock import patch
from app import application,workflow,participants,social,archive
from app.memory import MarkdownMemory
class ApplicationTests(unittest.TestCase):
 def test_worker_only_calls_exposed_api(self):
  self.assertEqual(social.validate_decision('{"call":{"name":"reply","arguments":{"text":"好","quality":"natural"}}}')['action'],'reply')
  # Unknown API name still rejected.
  with self.assertRaises(ValueError):application.decode(json.dumps({'call':{'name':'set_member','arguments':{}}}))
  # Unknown fields/arguments are dropped, not rejected.
  for raw in [{'action':'reply','text':'hi','speakers':[]},{'call':{'name':'reply','arguments':{'text':'hi','target_group':'999'}}},{'call':{'name':'wait','arguments':{}},'group_state':'overwrite'}]:
   result=application.decode(json.dumps(raw));self.assertIn(result['name'],('reply','wait','finish_turn'))
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
 def test_send_to_group_direct_call_carries_group_id(self):
  d=application.decision(application.decode(json.dumps({'call':{'name':'send_to_group','arguments':{'group_id':'970078128','text':'隔壁群来打个招呼','quality':'natural'}}})))
  self.assertEqual(d['action'],'send_to_group');self.assertEqual(d['group_id'],'970078128');self.assertEqual(d['text'],'隔壁群来打个招呼')
 def test_finish_turn_send_to_group_carries_group_id(self):
  d=application.decision(application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'send_to_group','group_id':'970078128','text':'主动去别的群说一句','quality':'natural'}}})))
  self.assertEqual(d['action'],'send_to_group');self.assertEqual(d['group_id'],'970078128')
 def test_send_to_group_requires_group_id_and_content(self):
  with self.assertRaises(ValueError):application.decision(application.decode(json.dumps({'call':{'name':'send_to_group','arguments':{'text':'no target','quality':'natural'}}})))
  with self.assertRaises(ValueError):application.decision(application.decode(json.dumps({'call':{'name':'send_to_group','arguments':{'group_id':'970078128','quality':'natural'}}})))
 def test_send_private_direct_call_carries_user_id(self):
  d=application.decision(application.decode(json.dumps({'call':{'name':'send_private','arguments':{'user_id':'12345','text':'私聊一句','quality':'natural'}}})))
  self.assertEqual(d['action'],'send_private');self.assertEqual(d['user_id'],'12345');self.assertEqual(d['text'],'私聊一句')
 def test_finish_turn_send_private_carries_user_id(self):
  d=application.decision(application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'send_private','user_id':'12345','text':'主动私信','quality':'natural'}}})))
  self.assertEqual(d['action'],'send_private');self.assertEqual(d['user_id'],'12345')
 def test_send_private_requires_user_id_and_content(self):
  with self.assertRaises(ValueError):application.decision(application.decode(json.dumps({'call':{'name':'send_private','arguments':{'text':'no user','quality':'natural'}}})))
  with self.assertRaises(ValueError):application.decision(application.decode(json.dumps({'call':{'name':'send_private','arguments':{'user_id':'12345','quality':'natural'}}})))
 def test_read_group_is_a_read_action(self):
  d=application.decision(application.decode(json.dumps({'call':{'name':'read_group','arguments':{'group_id':'970078128'}}})))
  self.assertEqual(d['action'],'read');self.assertEqual(d['call']['name'],'read_group')
 def test_settle_memory_summary_keeps_named_speakers_and_luna(self):
  with tempfile.TemporaryDirectory() as root:
   memory=MarkdownMemory(root)
   try:
    batch=[{'event_key':'1:1','created':1},{'event_key':'1:2','created':2}]
    views=[{'speaker':'1234567','nickname':'阿甲','text':'说了点事'},
           {'speaker':'self','nickname':'自己','text':'我回应了'}]
    summary=application.settle_memory(memory,'1',batch,views)
    self.assertIn('阿甲：',summary);self.assertIn('Luna：',summary)
    self.assertNotIn('1234567：',summary)
   finally:memory.close()
 def test_read_group_messages_carry_archive_nicknames(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root,'bot_id':'99','groups':[{'id':'1','name':'A'},{'id':'2','name':'B'}]}
   db=workflow.connect(':memory:');now=time.time()
   workflow.ingest(db,{'post_type':'message','message_type':'group','group_id':2,'user_id':7,'message_id':1,'time':now,'sender':{'nickname':'阿甲'},'message':[{'type':'text','data':{'text':'隔壁群消息'}}]},cfg,now)
   with db:db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('2','9','fp2','没有job的消息','[]',now))
   disk=archive.Archive(root)
   try:
    disk.record({'group_id':2,'user_id':7,'message_id':1,'time':now,'sender':{'card':'隔壁阿甲'},'message':[{'type':'text','data':{'text':'隔壁群消息'}}]})
    disk.record({'group_id':2,'user_id':8,'message_id':9,'time':now+1,'sender':{'card':'隔壁阿乙'},'message':[{'type':'text','data':{'text':'没有job的消息'}}]})
   finally:disk.close()
   result=application.read(db,cfg,'1',{},{'name':'read_group','arguments':{'group_id':'2'}})
   bymid={m['event_key']:m for m in result['messages']}
   self.assertEqual(bymid['2:1']['nickname'],'隔壁阿甲')
   self.assertEqual(bymid['2:9']['speaker'],'8');self.assertEqual(bymid['2:9']['nickname'],'隔壁阿乙')
   db.close()
 def test_member_kind_api_requires_real_evidence_and_preserves_known_bot(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root,'bot_id':'99'};db=workflow.connect(':memory:')
   for user in ('7','9'):
    with db:participants.observe(db,{'user_id':user,'group_id':'1','message_id':user,'time':1000},cfg,'真实样本',1000)
   payload={'member_profiles':[{'id':'7','current_judgment':{}},{'id':'9','current_judgment':{}}]}
   args={'members':[{'user_id':'7','state':'参与群聊','account_kind':'human','confidence':.9,'reason':'有上下文证据','evidence_keys':['1:fake']}]}
   with self.assertRaises(ValueError):application.update_states(cfg,'1',payload,args,db)
   args['members'][0]['evidence_keys']=['1:7'];application.update_states(cfg,'1',payload,args,db)
   self.assertEqual(db.execute("SELECT kind FROM speaker_profiles WHERE user_id='7'").fetchone()[0],'human')
   with db:participants.set_profile(db,'9','bot',.97,'已有证据','app_rules',1001)
   args['members'][0].update(user_id='9',evidence_keys=['1:9']);application.update_states(cfg,'1',payload,args,db)
   self.assertEqual(db.execute("SELECT kind FROM speaker_profiles WHERE user_id='9'").fetchone()[0],'bot');db.close()
