import hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import emoticons, workflow, moderation

class ModerationTests(unittest.TestCase):
 def setUp(self):
  self.db=workflow.connect(':memory:')
  self.cfg={'bot_id':1,'groups':[{'id':10}],'mode':'live','moderation':{'appeal_enabled':True}}
  self._no_model=patch('app.social.load_model',return_value={'enabled':False});self._no_model.start()
 def tearDown(self):
  self._no_model.stop();self.db.close()
 def notice(self,t=100,duration=500,sub='ban',operator=2):
  return {'post_type':'notice','notice_type':'group_ban','group_id':10,'user_id':1,'operator_id':operator,'time':t,'duration':duration,'sub_type':sub}
 def status(self):
  return self.db.execute('SELECT appeal_status,message_id,episode FROM bot_mutes').fetchone()
 def blocked(self,wording='你的消息已发出，但被对方拒收了'):
  error=ValueError('OneBot 操作未成功，retcode=100')
  error.retcode=100
  error.wording=wording
  return error
 def mock_api(self,members=None,friends=None,send=None):
  members=members or [{'user_id':2,'role':'owner'}]
  friends=[{'user_id':f} for f in friends] if friends is not None else [{'user_id':2}]
  targets=[]
  def call(_,action,payload=None):
   if action=='get_login_info':return {'user_id':1}
   if action=='get_group_member_list':return members
   if action=='get_friend_list':return friends
   if action=='send_private_msg':
    targets.append(payload['user_id'])
    if send is not None:
      result=send(payload)
      if isinstance(result,Exception):raise result
      return result
    return {'message_id':55}
   raise AssertionError(action)
  return call,targets
 def test_deduplicate_and_lift(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  self.assertTrue(moderation.is_muted(self.db,10,101))
  first_episode=self.status()[2]
  with self.db:self.db.execute("UPDATE bot_mutes SET appeal_status='sent'")
  moderation.ingest(self.db,self.notice(110),self.cfg,110)
  moderation.ingest(self.db,self.notice(120),self.cfg,120)
  self.assertEqual(self.status()[0],'sent')
  # Extending the mute never starts a new appeal episode.
  self.assertEqual(self.status()[2],first_episode)
  moderation.ingest(self.db,self.notice(130,0,'lift_ban'),self.cfg,130)
  self.assertFalse(moderation.is_muted(self.db,10,131))
  moderation.ingest(self.db,self.notice(300),self.cfg,300)
  self.assertEqual(self.status()[2],300)
 def test_only_once(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  call,targets=self.mock_api()
  with patch('app.moderation.agent.onebot_call',side_effect=call):
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),1)
   self.assertEqual(moderation.flush(self.db,self.cfg,{},102),0)
  self.assertEqual(targets,[2])
  self.assertEqual(self.status()[0],'sent')
  self.assertEqual(self.status()[1],'55')
 def test_rejected_falls_back_to_next_admin(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  members=[{'user_id':2,'role':'owner'},{'user_id':3,'role':'admin'}]
  def send(payload):
   return self.blocked() if payload['user_id']==2 else {'message_id':77}
  call,targets=self.mock_api(members=members,friends=[2,3],send=send)
  with patch('app.moderation.agent.onebot_call',side_effect=call):
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),0)
   self.assertEqual(self.status()[0],'seeking')
   self.assertEqual(moderation.flush(self.db,self.cfg,{},102),1)
  self.assertEqual(targets,[2,3])
  self.assertEqual(self.status()[0],'sent')
  self.assertEqual(self.status()[1],'77')
 def test_all_admins_reject_exhausts(self):
  moderation.ingest(self.db,self.notice(100,duration=100000),self.cfg,100)
  members=[{'user_id':2,'role':'owner'},{'user_id':3,'role':'admin'}]
  call,targets=self.mock_api(members=members,friends=[2,3],send=lambda payload:self.blocked())
  # Each admin is retried three times with a 5-minute backoff before being given up on.
  with patch('app.moderation.agent.onebot_call',side_effect=call):
   for t in (101,402,703,1004,1305,1606):
    self.assertEqual(moderation.flush(self.db,self.cfg,{},t),0)
  self.assertEqual(targets,[2,2,2,3,3,3])
  self.assertEqual(self.status()[0],'exhausted')
 def test_transient_retries_same_admin_then_exhausts(self):
  moderation.ingest(self.db,self.notice(100,duration=100000),self.cfg,100)
  call,targets=self.mock_api(send=lambda payload:TimeoutError())
  with patch('app.moderation.agent.onebot_call',side_effect=call):
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),0)
   self.assertEqual(moderation.flush(self.db,self.cfg,{},102),0)
   self.assertEqual(moderation.flush(self.db,self.cfg,{},402),0)
   self.assertEqual(moderation.flush(self.db,self.cfg,{},703),0)
   self.assertEqual(moderation.flush(self.db,self.cfg,{},704),0)
  self.assertEqual(targets,[2,2,2])
  self.assertEqual(self.status()[0],'exhausted')
 def test_new_episode_retries_after_previous_exhaustion(self):
  moderation.ingest(self.db,self.notice(100,duration=100000),self.cfg,100)
  call,targets=self.mock_api(send=lambda payload:self.blocked())
  with patch('app.moderation.agent.onebot_call',side_effect=call):
   for t in (101,402,703):
    moderation.flush(self.db,self.cfg,{},t)
  self.assertEqual(self.status()[0],'exhausted')
  moderation.ingest(self.db,self.notice(200,0,'lift_ban'),self.cfg,200)
  moderation.ingest(self.db,self.notice(300,duration=100000),self.cfg,300)
  with patch('app.moderation.agent.onebot_call',side_effect=self.mock_api()[0]):
   self.assertEqual(moderation.flush(self.db,self.cfg,{},301),1)
  self.assertEqual(self.status()[0],'sent')
 def test_no_friend_no_dm(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  call,targets=self.mock_api(friends=[])
  with patch('app.moderation.agent.onebot_call',side_effect=call):
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),0)
  self.assertEqual(targets,[])
  self.assertEqual(self.status()[0],'no_friend_admin')
 def test_non_friend_admin_skipped(self):
  moderation.ingest(self.db,self.notice(),self.cfg,100)
  members=[{'user_id':2,'role':'owner'},{'user_id':3,'role':'admin'}]
  call,targets=self.mock_api(members=members,friends=[3])
  with patch('app.moderation.agent.onebot_call',side_effect=call):
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),1)
  self.assertEqual(targets,[3])
 def test_appeal_text_generated_by_model(self):
  moderation.ingest(self.db,self.notice(100,duration=100000),self.cfg,100)
  sent=[]
  def send(payload):
   sent.append(payload['message'][0]['data']['text']);return {'message_id':88}
  call,targets=self.mock_api(send=send)
  model={'enabled':True,'model':'m','base_url':'http://x','api_key':'fake'}
  with patch('app.moderation.agent.onebot_call',side_effect=call),patch('app.social.load_model',return_value=model),patch('app.social.generate_text',return_value=('{"text":"大哥我哪句说错了你直接指，别一声不吭就禁言啊"}',{'prompt_tokens':9,'completion_tokens':7})) as gen:
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),1)
  self.assertEqual(sent,['大哥我哪句说错了你直接指，别一声不吭就禁言啊'])
  payload=gen.call_args.args[2]
  self.assertEqual(payload['conversation'],'mute_appeal')
  self.assertEqual(payload['operator']['user_id'],'2')
 def test_appeal_falls_back_when_model_unavailable(self):
  moderation.ingest(self.db,self.notice(100,duration=100000),self.cfg,100)
  sent=[]
  call,targets=self.mock_api(send=lambda p:(sent.append(p['message'][0]['data']['text']),{'message_id':88})[1])
  model={'enabled':True,'model':'m','base_url':'http://x','api_key':'fake'}
  with patch('app.moderation.agent.onebot_call',side_effect=call),patch('app.social.load_model',return_value=model),patch('app.social.generate_text',side_effect=TimeoutError()):
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),1)
  self.assertEqual(sent,[moderation.APPEAL])
 def test_appeal_uses_global_sticker_as_second_message(self):
  tmp=tempfile.TemporaryDirectory();root=Path(tmp.name)
  key=hashlib.sha256(b'cross-group-sticker').hexdigest()+'.jpg'
  (root/'media').mkdir();(root/'media'/key).write_bytes(b'sticker')
  # Sticker was collected in another group; the appeal pool is global.
  identity=emoticons.learn(root,'999','','嘲讽大表情',key,tags=['嘲讽','鄙视'])
  cfg=dict(self.cfg);cfg['context_dir']=str(root)
  moderation.ingest(self.db,self.notice(100,duration=100000),cfg,100)
  messages=[]
  def call(_,action,payload=None):
   if action=='get_login_info':return {'user_id':1}
   if action=='get_group_member_list':return [{'user_id':2,'role':'owner'}]
   if action=='get_friend_list':return [{'user_id':2}]
   if action=='send_private_msg':messages.append(payload['message']);return {'message_id':77}
   raise AssertionError(action)
  model={'enabled':True,'model':'m','base_url':'http://x','api_key':'fake'}
  reply='{"text":"哪条群规？给个说法","sticker_id":"%s"}'%identity
  with patch('app.moderation.agent.onebot_call',side_effect=call),patch('app.social.load_model',return_value=model),patch('app.social.generate_text',return_value=(reply,{'prompt_tokens':9,'completion_tokens':7})):
   self.assertEqual(moderation.flush(self.db,cfg,{},101),1)
  self.assertEqual(len(messages),2)
  self.assertEqual([p['type'] for p in messages[0]],['text'])
  self.assertEqual(messages[0][0]['data']['text'],'哪条群规？给个说法')
  self.assertEqual([p['type'] for p in messages[1]],['image'])
  self.assertTrue(messages[1][0]['data']['file'].startswith('base64://'))
  tmp.cleanup()
 def test_half_hour_check_reopens_appeal(self):
  moderation.ingest(self.db,self.notice(100,duration=100000),self.cfg,100)
  members=[{'user_id':1,'shut_up_timestamp':99999},{'user_id':2,'role':'owner'}]
  call,targets=self.mock_api(members=members)
  with patch('app.moderation.agent.onebot_call',side_effect=call) as mocked:
   # First appeal goes out immediately.
   self.assertEqual(moderation.flush(self.db,self.cfg,{},101),1)
   self.assertEqual(self.status()[0],'sent')
   # Before the half-hour check nothing is sent again.
   moderation.check_recovery(self.db,self.cfg,{},101)
   self.assertEqual(self.status()[0],'sent')
   # At the half-hour check the live status still shows muted: appeal reopens.
   moderation.check_recovery(self.db,self.cfg,{},1900)
   self.assertEqual(self.status()[0],'pending')
   self.assertEqual(self.db.execute('SELECT count(*) FROM mute_appeal_attempts').fetchone()[0],0)
   self.assertEqual(moderation.flush(self.db,self.cfg,{},1900),1)
   # Next check is scheduled another half hour out.
   moderation.check_recovery(self.db,self.cfg,{},1901)
   self.assertEqual(self.status()[0],'sent')
  self.assertEqual(targets,[2,2])
  self.assertEqual(sum(c.args[1]=='send_private_msg' for c in mocked.call_args_list),2)
if __name__=='__main__':
 unittest.main()
