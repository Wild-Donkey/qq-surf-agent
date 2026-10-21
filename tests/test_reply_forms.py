import unittest,tempfile,time,json
from unittest.mock import patch
import workflow,social,application,replies,emoticons

class ReplyFormsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','context_dir':self.tmp.name,'groups':[{'id':1,'name':'A'}],'mode':'live','reply_delay_seconds':0}
  with self.db:self.db.execute('INSERT INTO group_activity VALUES (?,?)',('1',self.now))
  self.face=next(iter(emoticons.FACES));self.job={'source_group':'1','target_group':'1','event_key':'1:hello','trigger_text':'hello'}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_ordered_reply_uses_one_send_and_one_receipt(self):
  parts=[{'emoticon':{'kind':'face','id':self.face}},{'text':'绷不住了'},{'emoticon':{'kind':'face','id':self.face}}]
  d=application.decision(application.decode(json.dumps({'call':{'name':'finish_turn','arguments':{'action':'reply','reply_parts':parts,'quality':'natural'}}})))
  cid=social.apply_decision(self.db,self.job,d,{},self.cfg,self.now)
  self.assertIsNotNone(cid)
  with patch('participants.humans_recent',return_value=True),patch('workflow.newer_context',return_value=False),patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':42}]) as api:
   self.assertEqual(workflow.dispatch(self.db,self.cfg,{},self.now+1),1)
  sent=[c.args[2] for c in api.call_args_list if c.args[1]=='send_group_msg']
  self.assertEqual(len(sent),1);self.assertEqual([p['type'] for p in sent[0]['message']],['face','text','face'])
  self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'sent')
 def test_reaction_sequence_can_have_no_text(self):
  d={'action':'reply','text':'','reply_parts':[{'emoticon':{'kind':'face','id':self.face}}]}
  self.assertIsNotNone(social.apply_decision(self.db,self.job,d,{},self.cfg,self.now))
 def test_invalid_or_excessive_sequence_is_not_queued(self):
  for parts in ([{'emoticon':{'kind':'sticker','id':'unknown'}}],[{'text':'x'}]*9):
   d={'action':'reply','text':'','reply_parts':parts}
   self.assertIsNone(social.apply_decision(self.db,self.job,d,{},self.cfg,self.now));self.assertEqual(d['action'],'wait')
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
 def test_parts_fingerprint_distinguishes_reactions_and_deduplicates_restarts(self):
  for _ in range(2):
   social.apply_decision(self.db,self.job,{'action':'reply','text':'','reply_parts':[{'emoticon':{'kind':'face','id':self.face}}]}, {},self.cfg,self.now)
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
 def test_recent_sticker_is_rejected(self):
  with patch('emoticons.safe_selection',return_value={'kind':'sticker','id':'x'}),patch('emoticons.recent_echo',return_value=True):
   with self.assertRaises(ValueError):replies.prepare(self.cfg,'1',[{'emoticon':{'kind':'sticker','id':'x'}}])
