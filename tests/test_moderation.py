import unittest
from unittest.mock import patch
import workflow,moderation

class ModerationTests(unittest.TestCase):
 def setUp(self):
  self.db=workflow.connect(':memory:')
  self.cfg={'bot_id':1,'groups':[{'id':10}],'mode':'live','moderation':{'appeal_enabled':True}}
 def tearDown(self):self.db.close()
 def notice(self,t=100,duration=500,sub='ban'):
  return {'post_type':'notice','notice_type':'group_ban','group_id':10,'user_id':1,'operator_id':2,'time':t,'duration':duration,'sub_type':sub}
 def test_deduplicate_and_lift(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  self.assertTrue(moderation.is_muted(self.db,10,101))
  with self.db:self.db.execute("UPDATE bot_mutes SET appeal_status='sent'")
  moderation.ingest(self.db,self.notice(),self.cfg,110)
  moderation.ingest(self.db,self.notice(120),self.cfg,120)
  self.assertEqual(self.db.execute('SELECT appeal_status FROM bot_mutes').fetchone()[0],'sent')
  moderation.ingest(self.db,self.notice(130,0,'lift_ban'),self.cfg,130)
  self.assertFalse(moderation.is_muted(self.db,10,131))
 def test_only_once(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  def call(api,action,payload=None):
   return {'get_login_info':{'user_id':1},'get_group_member_list':[{'user_id':2,'role':'owner'}],'get_friend_list':[{'user_id':2}],'send_private_msg':{'message_id':55}}[action]
  with patch('moderation.agent.onebot_call',side_effect=call) as mock:
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),1)
   self.assertEqual(moderation.flush(self.db,self.cfg,{},102),0)
   self.assertEqual(sum(c.args[1]=='send_private_msg' for c in mock.call_args_list),1)
 def test_failure_not_retried(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  with patch('moderation.agent.onebot_call',side_effect=TimeoutError) as mock:
   moderation.flush(self.db,self.cfg,{},101)
   moderation.flush(self.db,self.cfg,{},102)
   self.assertEqual(mock.call_count,1)
 def test_no_friend_no_dm(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  def call(api,action,payload=None):
   return {'get_login_info':{'user_id':1},'get_group_member_list':[{'user_id':2,'role':'owner'}],'get_friend_list':[]}[action]
  with patch('moderation.agent.onebot_call',side_effect=call) as mock:
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),0)
   self.assertFalse(any(c.args[1]=='send_private_msg' for c in mock.call_args_list))
