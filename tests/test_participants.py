"""participants, human_guard, identities, profiling regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time
import unittest
import workflow
import participants
from unittest.mock import patch
import social
import json
import sqlite3
import tempfile
import identities
import application
import profiling
import archive
import model_gate


class ParticipantTests(unittest.TestCase):
    def setUp(self):
        self.db=workflow.connect(':memory:')
        self.cfg={'bot_id':'99','groups':[{'id':'1','name':'A'}],
                  'human_presence':{'enabled':True,'known_humans':['7'],'known_bots':['8'],'timeout_seconds':300}}
    def tearDown(self):self.db.close()
    def observe(self,user,stamp):
        event={'group_id':1,'user_id':user,'message_id':str(user)+':'+str(stamp),'time':stamp}
        with self.db:participants.observe(self.db,event,self.cfg,'hello',stamp)
    def test_only_known_bots_do_not_extend_human_window(self):
        # Unknown speakers count as human presence until identified as bots.
        self.observe(7,1000);self.observe(8,1299);self.observe(9,1299)
        self.assertTrue(participants.humans_recent(self.db,'1',self.cfg,1299))
        # Human 7 timed out; unknown 9 keeps the gate open.
        self.assertTrue(participants.humans_recent(self.db,'1',self.cfg,1300))
        self.observe(8,1600)
        # Bot 8 alone does not open the gate; user 7 at 1000 and user 9 at 1299 are both >300s ago by 1601.
        self.assertFalse(participants.humans_recent(self.db,'1',self.cfg,1601))
        self.observe(7,1602)
        self.assertTrue(participants.humans_recent(self.db,'1',self.cfg,1602))
    def test_reclassification_invalidates_past_human_activity(self):
        self.observe(9,1000);batch=[{'user_id':'9'}]
        with self.db:participants.apply_judgments(self.db,{'speakers':[{'id':'9','kind':'human','confidence':0.9}]},batch,self.cfg,1001)
        self.assertTrue(participants.humans_recent(self.db,'1',self.cfg,1001))
        with self.db:participants.apply_judgments(self.db,{'speakers':[{'id':'9','kind':'bot','confidence':0.95}]},batch,self.cfg,1002)
        self.assertFalse(participants.humans_recent(self.db,'1',self.cfg,1002))
    def test_unknown_opens_gate_while_unseen_ids_stay_unjudged(self):
        self.observe(8,1000);self.observe(9,1000)
        with self.db:participants.apply_judgments(self.db,{'speakers':[{'id':'8','kind':'human','confidence':1},{'id':'9','kind':'human','confidence':0.5},{'id':'123','kind':'human','confidence':1}]},[{'user_id':'8'},{'user_id':'9'}],self.cfg,1001)
        # Known-bot 8 stays bot; unknown 9 opens the gate.
        self.assertTrue(participants.humans_recent(self.db,'1',self.cfg,1001))
        self.assertFalse(self.db.execute("SELECT 1 FROM speaker_profiles WHERE user_id='123'").fetchone())
    def test_full_context_can_correct_a_silent_bot(self):
        self.observe(9,1000)
        with self.db:
            participants.apply_judgments(self.db,{'speakers':[{'id':'9','kind':'human','confidence':.86,'reason':'口语自然'}]},[{'user_id':'9'}],self.cfg,1001)
            participants.apply_judgments(self.db,{'speakers':[{'id':'9','kind':'bot','confidence':.97,'reason':'群友明确讨论该账号触发规则及清理上下文'}]},[{'user_id':'7'}],self.cfg,1002,{'9'})
        self.assertEqual(self.db.execute("SELECT kind FROM speaker_profiles WHERE user_id='9'").fetchone()[0],'bot')
        self.assertFalse(participants.humans_recent(self.db,'1',self.cfg,1002))
        self.assertEqual(self.db.execute("SELECT count(*) FROM speaker_judgments WHERE user_id='9'").fetchone()[0],3)
    def test_fluent_speech_cannot_flip_established_bot_back_to_human(self):
        self.observe(9,1000)
        with self.db:
            participants.apply_judgments(self.db,{'speakers':[{'id':'9','kind':'bot','confidence':.97}]},[{'user_id':'9'}],self.cfg,1001)
            participants.apply_judgments(self.db,{'speakers':[{'id':'9','kind':'human','confidence':.86,'reason':'自然聊天'}]},[{'user_id':'9'}],self.cfg,1002)
        self.assertEqual(self.db.execute("SELECT kind FROM speaker_profiles WHERE user_id='9'").fetchone()[0],'bot')
    def test_source_is_exposed_in_evidence(self):
        self.observe(9,1000);self.observe(7,1000)
        evidence={v['id']:v for v in participants.evidence(self.db,[{'user_id':'9'},{'user_id':'7'}])}
        self.assertEqual(evidence['9']['current_judgment']['source'],'initial')
        self.assertEqual(evidence['7']['current_judgment']['source'],'config')


class HumanGuardTests(unittest.TestCase):
    def test_approved_reply_cannot_send_without_recent_human(self):
        db=workflow.connect(':memory:');now=time.time()
        cfg={'bot_id':'99','mode':'live','reply_delay_seconds':0,'groups':[{'id':'1','name':'A'}],
             'human_presence':{'enabled':True,'known_bots':['8']}}
        with db:
            db.execute('INSERT INTO group_activity VALUES (?,?)',('1',now))
            participants.observe(db,{'group_id':1,'user_id':8,'message_id':1,'time':now},cfg,'自动播报',now)
        social.apply_decision(db,{'target_group':'1','source_group':'1','event_key':'1:1'},
                              {'action':'reply','text':'不该发出'}, {},cfg,now)
        with patch('agent.onebot_call',return_value={'user_id':99}) as api:
            self.assertEqual(workflow.dispatch(db,cfg,{},now+1),0)
            self.assertEqual(api.call_count,1)
        self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],'approved');db.close()


class IdentityTests(unittest.TestCase):
 def test_width_stable_global_and_collision(self):
  with tempfile.TemporaryDirectory() as root:
   r=identities.Registry(root)
   self.assertEqual(identities.short('一二三四五六'),'一二三四五')
   self.assertEqual(identities.short('abcdefghijk'),'abcdefghij')
   self.assertEqual(identities.short('ab一二三四五'),'ab一二三四')
   self.assertEqual(r.alias('1','MyCall'),'MyCall')
   self.assertEqual(r.alias('1','另一个群名片'),'MyCall')
   self.assertNotEqual(r.alias('2','MyCall'),'MyCall')
   r.set('1','老崔','observed');r.close();r=identities.Registry(root)
   self.assertEqual(r.alias('1'),'老崔');self.assertEqual(r.resolve('老崔'),'1');r.close()
 def test_initial_uses_account_name_not_card(self):
  with tempfile.TemporaryDirectory() as root:
   db=sqlite3.connect(Path(root)/'archive.sqlite3');db.execute('CREATE TABLE messages(id INTEGER,user_id TEXT,nickname TEXT,raw_json TEXT)')
   db.execute('INSERT INTO messages VALUES (1,?,?,?)',('1','群名片',json.dumps({'sender':{'nickname':'MyCall'}})));db.commit();db.close()
   r=identities.Registry(root);self.assertEqual(r.alias('1','群名片'),'MyCall');r.close()
 def test_transport_compact_quotes_history_and_on_demand_details(self):
  with tempfile.TemporaryDirectory() as root:
   payload={'_identity_root':root,'member_memories':[{'user_id':'1','markdown':'private profile'}],'member_profiles':[{'id':'1'}],
    'unread_messages':[{'speaker':'1','nickname':'MyCall','text':'hi','event_key':'g:1','reply_to':{'speaker':'2','nickname':'老崔','text':'hello'},'mentions':['2']}],
    'last_two_hours':{'messages':[{'user_id':'1','nickname':'card','text':'earlier'}]},
    'tool_results':[{'call':{'name':'get_member'},'result':{'user_id':'1','memory':'requested'}}]}
   out=identities.worker_payload(payload)
   self.assertEqual(out['unread_messages'][0]['line'],'MyCall：hi')
   self.assertEqual(out['unread_messages'][0]['reply_to']['line'],'老崔：hello')
   self.assertEqual(out['last_two_hours']['messages'][0]['line'],'MyCall：earlier')
   self.assertNotIn('member_memories',out);self.assertNotIn('nickname',out['unread_messages'][0])
   self.assertEqual(out['tool_results'][0]['result']['user_id'],'1')
   self.assertEqual(payload['unread_messages'][0]['speaker'],'1')
 def test_alias_updates_require_visible_evidence_and_context(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root};db=workflow.connect(':memory:');participants.ensure_schema(db)
   r=identities.Registry(root);r.alias('1','MyCall');r.close()
   payload={'member_profiles':[{'id':'1'}],'unread_messages':[{'event_key':'g:1','text':'大家都叫你老崔'}]}
   call={'name':'set_member_alias','arguments':{'alias':'MyCall','name':'老崔','evidence_event_key':'g:1'}}
   self.assertEqual(application.read(db,cfg,'g',payload,call),{'alias':'老崔'})
   call['arguments'].update(alias='老崔',name='凭空取名')
   with self.assertRaises(ValueError):application.read(db,cfg,'g',payload,call)
   db.close()

 def test_member_query_returns_requested_details_only(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');participants.ensure_schema(db)
   r=identities.Registry(root);r.alias('1','老崔');r.close()
   with patch('agent.onebot_call',return_value={'user_id':1,'nickname':'account','card':'group card'}):
    result=application.read(db,{'context_dir':root},'9',{'member_profiles':[{'id':'1'}]}, {'name':'get_member','arguments':{'alias':'老崔'}})
   self.assertEqual(result['user_id'],'1');self.assertEqual(result['account']['card'],'group card')
   schema={'call':{'name':'get_memory_schema'},'result':{'properties':{'user_id':{'type':'string'}}}}
   out=identities.worker_payload({'_identity_root':root,'tool_results':[schema]})
   self.assertEqual(out['tool_results'][0],schema);db.close()


class ProfilingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.cfg={'context_dir':self.tmp.name,'bot_id':'99','groups':[{'id':'1','name':'A'}],'profiling':{'max_messages':5000}}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def event(self,mid,stamp,user=7):return {'group_id':1,'message_id':mid,'message_seq':mid,'user_id':user,'time':stamp,'sender':{'nickname':'群友'+str(user)},'message':[{'type':'text','data':{'text':'完整原话'+str(mid)+'甲'*300}}]}
 def test_pagination_preserves_full_history_and_detects_repeat(self):
  profiling.enqueue(self.db,self.cfg,'1');state={'phase':'fetch'};now=time.time();events=[self.event(900,now-100),self.event(1,now)]
  with patch('agent.onebot_call',return_value={'messages':events}) as api:
   profiling.fetch_page(self.db,self.cfg,{},'1',state);self.assertEqual(state['cursor'],'900')
   profiling.fetch_page(self.db,self.cfg,{},'1',state)
   self.assertEqual(api.call_args[0][2]['message_seq'],'900')
  self.assertEqual(state['total'],2);self.assertEqual(state['phase'],'group_map')
 def test_group_written_before_members_and_all_chunks_consumed(self):
  now=time.time();disk=archive.Archive(self.tmp.name)
  for i in range(150):disk.record(self.event(i+1,now-200+i,7+i%2))
  disk.close();profiling.enqueue(self.db,self.cfg,'1')
  rows=profiling.snapshot(self.cfg,'1');state={'phase':'group_map','pages':2,'total':len(rows),'messages':rows,'group_chunks':profiling.chunks(rows),'group_notes':[],'chunk_index':0}
  profiling.save(self.db,self.cfg,'1',state)
  group_seen=[];member_seen=[]
  def invoke(operation,payload):
   if operation==profiling.GROUP_MAP and payload['scope']=='group':group_seen.extend(r['event_key'] for r in payload['messages'])
   if operation==profiling.MEMBER_FINAL:
    self.assertTrue((Path(self.tmp.name)/'groups/1/group.md').exists());self.assertIn('综合群画像',payload['group_profile']);member_seen.append(payload['target_user_id'])
   return '综合群画像' if operation==profiling.GROUP_FINAL else '完整成员画像' if operation==profiling.MEMBER_FINAL else '分块观察与证据'
  for _ in range(30):
   profiling.tick(self.db,self.cfg,{},invoke)
   if self.db.execute('SELECT status FROM profile_jobs').fetchone()[0]=='done':break
  self.assertEqual(len(group_seen),150);self.assertEqual(len(set(group_seen)),150);self.assertEqual(set(member_seen),{'7','8'})
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
  self.assertIn('完整成员画像',application.member_markdown(self.cfg,'1','7'))
  self.assertNotIn('完整成员画像',application.member_markdown(self.cfg,'2','7'))
 def test_failed_chunk_resumes_without_advancing(self):
  profiling.enqueue(self.db,self.cfg,'1');state={'phase':'group_map','pages':1,'total':1,'messages':[],'group_chunks':[[{'speaker':'7','text':'hello'}]],'group_notes':[],'chunk_index':0};profiling.save(self.db,self.cfg,'1',state)
  def fail(*args):raise ValueError('failed')
  with self.assertRaises(ValueError):profiling.tick(self.db,self.cfg,{},fail)
  self.assertEqual(json.loads(profiling.state_path(self.cfg,'1').read_text())['chunk_index'],0)
 def test_background_and_chat_share_atomic_start_budget(self):
  first=model_gate.reserve(self.db,self.cfg,1000);self.assertIsNotNone(first)
  self.assertIsNone(model_gate.reserve(self.db,self.cfg,1001,background=True));self.assertIsNotNone(model_gate.reserve(self.db,self.cfg,1005,background=True))


if __name__ == '__main__':
    unittest.main()


class WorkerRoleContextTests(unittest.TestCase):
 def test_compact_handles_retain_owner_peer_bot_and_self_identity(self):
  import identities
  with tempfile.TemporaryDirectory() as root:
   payload={'_identity_root':root,'_identity_bot':'9','_identity_owner':'1',
    'member_profiles':[{'id':'1','current_judgment':{'kind':'human','source':'config'}},{'id':'2','current_judgment':{'kind':'bot','source':'app_rules'}}],
    'member_memories':[{'user_id':'2','markdown':'large profile'}],'group_context':{'id':'3','name':'测试群'},'group_portrait':{'summary':'聊技术'},
    'unread_messages':[{'speaker':'1','nickname':'MyCall','text':'我的模型在线'},{'speaker':'2','nickname':'鲸鲸','text':'我还要睡觉'},{'speaker':'9','nickname':'NOIP','text':'自己说过的话'}]}
   result=identities.worker_payload(payload)
   roles={row['alias']:row for row in result['group_context']['participants']}
   self.assertEqual(roles['MyCall']['role'],'owner');self.assertEqual(roles['鲸鲸']['account_kind'],'bot')
   self.assertEqual(result['bot_identity']['owner_alias'],'MyCall')
   self.assertEqual(result['unread_messages'][-1]['line'],'Luna：自己说过的话')
   self.assertNotIn('member_memories',result);self.assertNotIn('_identity_owner',result)
   self.assertNotIn('participant_roles',result);self.assertNotIn('self_context',result)
   self.assertEqual(result['group_context']['portrait'],{'summary':'聊技术'})
   self.assertEqual(result['group_context']['name'],'测试群')
   self.assertNotIn('group_portrait',result)
