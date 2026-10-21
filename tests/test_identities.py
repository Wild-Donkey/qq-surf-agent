import json,sqlite3,tempfile,unittest
from pathlib import Path
import identities,application,workflow,participants

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
