import json,time,unittest,tempfile,urllib.error
from unittest.mock import patch
import application,social,workflow
class ReviewFixTests(unittest.TestCase):
 def test_nested_envelopes_and_conflicts(self):
  good={'action':'reply','text':'好','quality':'natural'}
  self.assertEqual(social.validate_decision(json.dumps({'type':'json_object','content':json.dumps(good)}))['action'],'reply')
  with self.assertRaises(ValueError):
   application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'wait','quality':'awkward'}},'quality':'natural'}))
  with self.assertRaises(ValueError):
   application.decode(json.dumps({'call':{'name':'wait','arguments':{}},'action':'reply'}))
 def test_transport_errors_are_not_retried(self):
  exc=urllib.error.HTTPError('https://example.com',429,'quota',{},None)
  with patch('social.generate_text',side_effect=exc) as gen:
   with self.assertRaises(urllib.error.HTTPError):social.call_model({}, {})
   self.assertEqual(gen.call_count,1)
 def test_retry_reserved_accounted_and_forced_low(self):
  calls=[];done=[]
  def generate(model,*args):
   calls.append(model)
   return ('bad' if len(calls)==1 else '{"call":{"name":"wait","arguments":{}}}'),{'prompt_tokens':10,'completion_tokens':2}
  model={'reasoning_effort':'none','_retry_reserve':lambda usage:42,'_retry_complete':lambda *args:done.append(args)}
  with patch('social.generate_text',side_effect=generate):d,u=social.call_model(model,{})
  self.assertEqual([c['reasoning_effort'] for c in calls],['none','low'])
  self.assertEqual(done[0][:2],(42,True));self.assertEqual(done[0][2]['prompt_tokens'],10);self.assertEqual(u['prompt_tokens'],10)
 def test_standalone_invalid_output_does_not_make_unreserved_retry(self):
  with patch('social.generate_text',return_value=('bad',{})) as gen:
   with self.assertRaises(ValueError):social.call_model({}, {})
   self.assertEqual(gen.call_count,1)
 def test_forward_scope_and_common_send_limit(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');now=time.time()
   cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'A'}],'context_dir':root,'hold_seconds':0,'max_per_tick':1,'group_cooldown_seconds':0,'max_per_group_day':None,'max_total_day':None}
   job={'target_group':'1','source_group':'1','event_key':'1:1'}
   with db:
    db.execute('INSERT INTO group_activity VALUES (?,?)',('1',now))
    for mid in ('1','2'):db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('1',mid,'f'+mid,'content','[]',now-2))
   with self.assertRaises(ValueError):social.apply_decision(db,job,{'action':'forward','message_id':'other'},{},cfg,now)
   for mid in ('1','2'):
    social.apply_decision(db,dict(job,event_key='1:'+mid),{'action':'forward','message_id':mid,'_forward_message_ids':[mid],'_context_watermark':now,'_deadline_reply':True},{},cfg,now)
   with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':123}]) as api:
    self.assertEqual(workflow.dispatch(db,cfg,{},now+1),1)
    self.assertEqual(api.call_count,2);self.assertEqual(api.call_args.args[1],'send_group_forward_msg')
   self.assertEqual(db.execute("SELECT count(*) FROM deliveries WHERE status='sent'").fetchone()[0],1)
   self.assertEqual(db.execute("SELECT count(*) FROM events WHERE message_id='bot:123'").fetchone()[0],1);db.close()
 def test_old_normal_draft_is_not_force_sent(self):
  db=workflow.connect(':memory:');now=time.time();cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'A'}]}
  with db:db.execute('INSERT INTO group_activity VALUES (?,?)',('1',now))
  social.apply_decision(db,{'target_group':'1','source_group':'1','event_key':'1:old'},{'action':'reply','text':'old'},{},cfg,now-30)
  with db:db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('1','new','f','new','[]',now))
  with patch('agent.onebot_call',return_value={'user_id':99}):self.assertEqual(workflow.dispatch(db,cfg,{},now),0)
  self.assertEqual(db.execute('SELECT status FROM deliveries').fetchone()[0],'superseded');db.close()
