import json,tempfile,time,unittest
from unittest.mock import patch
import private_chat,workflow,moderation

class PrivateTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':99,'mode':'live','groups':[{'id':1}],'context_dir':self.tmp.name,'private_chat':{'enabled':True}}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def add(self,uid=7,mid=1,text='你是谁'):
  e={'post_type':'message','message_type':'private','sub_type':'friend','user_id':uid,'message_id':mid,'time':self.now-10,'message':[{'type':'text','data':{'text':text}}]}
  workflow.ingest(self.db,e,self.cfg,self.now-10)
 def call(self,api,action,payload=None):
  return {'get_friend_list':[{'user_id':7},{'user_id':8}],'get_login_info':{'user_id':99},'send_private_msg':{'message_id':55}}[action]
 def test_private_never_enters_group_queue_and_deduplicates(self):
  self.add();self.add()
  self.assertEqual(self.db.execute('select count(*) from private_messages').fetchone()[0],1)
  self.assertEqual(self.db.execute('select count(*) from social_jobs').fetchone()[0],0)
 def test_recipient_bound_context_and_receipt(self):
  self.add();self.add(8,2,'另一个人的秘密')
  raw=json.dumps({'call':{'name':'finish_turn','arguments':{'action':'reply','text':'我是Luna，一个AI群友','quality':'natural'}}})
  with patch('private_chat.agent.onebot_call',side_effect=self.call) as api,patch('private_chat.social.load_model',return_value={'enabled':True,'api_key':'fake'}),patch('private_chat.social.generate_text',return_value=(raw,{'prompt_tokens':11,'completion_tokens':5})) as model:
   private_chat.tick(self.db,self.cfg,{},self.now)
   payload=model.call_args.args[2]
   self.assertNotIn('另一个人的秘密',json.dumps(payload,ensure_ascii=False))
   sent=[c.args[2] for c in api.call_args_list if c.args[1]=='send_private_msg']
   self.assertEqual(sent[0]['user_id'],7)
   self.assertEqual(self.db.execute('select status from private_deliveries').fetchone()[0],'sent')
 def test_uncertain_send_not_retried(self):
  self.add()
  def api(a,b,c=None):
   if b=='send_private_msg':raise TimeoutError()
   return self.call(a,b,c)
  raw=json.dumps({'action':'reply','text':'你好','quality':'natural'})
  with patch('private_chat.agent.onebot_call',side_effect=api) as mocked,patch('private_chat.social.load_model',return_value={'enabled':True,'api_key':'fake'}),patch('private_chat.social.generate_text',return_value=(raw,{})):
   private_chat.tick(self.db,self.cfg,{},self.now)
   private_chat.tick(self.db,self.cfg,{},self.now+100)
   self.assertEqual(sum(c.args[1]=='send_private_msg' for c in mocked.call_args_list),1)
 def test_recovery_poll_deduplicates_and_does_not_claim_without_metadata(self):
  moderation.ingest(self.db,{'post_type':'notice','notice_type':'group_ban','sub_type':'ban','group_id':1,'user_id':99,'operator_id':8,'time':self.now,'duration':500},self.cfg,self.now)
  def api(a,b,c=None):return {'user_id':99} if b=='get_login_info' else [{'user_id':99,'shut_up_timestamp':0}]
  with patch('moderation.agent.onebot_call',side_effect=api) as mocked,patch('participants.humans_recent',return_value=True):
   moderation.check_recovery(self.db,self.cfg,{},self.now+1800)
   moderation.check_recovery(self.db,self.cfg,{},self.now+1801)
   self.assertEqual(mocked.call_count,2)
   self.assertEqual(self.db.execute('select count(*) from deliveries').fetchone()[0],1)
   self.assertFalse(moderation.is_muted(self.db,1,self.now+1801))
 def test_still_muted_never_queues_return(self):
  moderation.ingest(self.db,{'post_type':'notice','notice_type':'group_ban','sub_type':'ban','group_id':1,'user_id':99,'operator_id':8,'time':self.now,'duration':100000},self.cfg,self.now)
  def api(a,b,c=None):return {'user_id':99} if b=='get_login_info' else [{'user_id':99,'shut_up_timestamp':self.now+100000}]
  with patch('moderation.agent.onebot_call',side_effect=api):moderation.check_recovery(self.db,self.cfg,{},self.now+1800)
  self.assertEqual(self.db.execute('select count(*) from deliveries').fetchone()[0],0)

 def test_departed_group_stops_polling_without_return(self):
  moderation.ingest(self.db,{'post_type':'notice','notice_type':'group_ban','sub_type':'ban','group_id':1,'user_id':99,'operator_id':8,'time':self.now,'duration':500},self.cfg,self.now)
  with self.db:self.db.execute('UPDATE bot_mutes SET next_check=0')
  def api(a,b,c=None):return {'user_id':99} if b=='get_login_info' else []
  with patch('moderation.agent.onebot_call',side_effect=api) as mocked:
   moderation.check_recovery(self.db,self.cfg,{},self.now+1)
   moderation.check_recovery(self.db,self.cfg,{},self.now+4000)
   self.assertEqual(mocked.call_count,3)
  row=self.db.execute('SELECT return_status,appeal_status,until_time FROM bot_mutes').fetchone()
  self.assertEqual(tuple(row),('left_group','cancelled',0))
  self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
 def test_incomplete_member_list_does_not_claim_departure(self):
  moderation.ingest(self.db,{'post_type':'notice','notice_type':'group_ban','sub_type':'ban','group_id':1,'user_id':99,'operator_id':8,'time':self.now,'duration':500},self.cfg,self.now)
  with self.db:self.db.execute('UPDATE bot_mutes SET next_check=0')
  def api(a,b,c=None):return {'get_login_info':{'user_id':99},'get_group_member_list':[],'get_group_list':[{'group_id':1}]}[b]
  with patch('moderation.agent.onebot_call',side_effect=api):moderation.check_recovery(self.db,self.cfg,{},self.now+1)
  self.assertNotEqual(self.db.execute('SELECT return_status FROM bot_mutes').fetchone()[0],'left_group')
 def test_private_web_search_then_reply_stays_recipient_bound(self):
  self.add(text='帮我搜索Python官网')
  search=json.dumps({'call':{'name':'web_search','arguments':{'query':'Python 官网'}}})
  reply=json.dumps({'call':{'name':'finish_turn','arguments':{'action':'reply','text':'官网 https://www.python.org/','quality':'natural'}}})
  with patch('private_chat.agent.onebot_call',side_effect=self.call) as api,patch('private_chat.social.load_model',return_value={'enabled':True,'api_key':'fake'}),patch('private_chat.social.generate_text',side_effect=[(search,{}),(reply,{})]) as model,patch('private_chat.model_gate.reserve',side_effect=[1,2]),patch('internet.tool',return_value={'results':[{'url':'https://www.python.org/'}]}),patch('private_chat.time.sleep'):
   private_chat.tick(self.db,self.cfg,{},self.now)
  self.assertEqual(model.call_count,2)
  sent=[c.args[2] for c in api.call_args_list if c.args[1]=='send_private_msg']
  self.assertEqual(sent[0]['user_id'],7)
