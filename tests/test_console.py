import concurrent.futures,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import console,operator_control,workflow,service_log,social

class ConsoleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'runtime').mkdir();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','groups':[{'id':'1','name':'测试群'}],'context_dir':str(self.root/'context'),'alerts':{'owner_qq':7}}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_tail_rotation_utf8_and_partial(self):
  p=self.root/'log';p.write_bytes('第一条\n半'.encode());tail=console.Tail(p)
  self.assertEqual(tail.read(),['第一条'])
  with p.open('ab') as f:f.write('行\n'.encode())
  self.assertEqual(tail.read(),['半行'])
  p.rename(self.root/'old');p.write_text('新文件\n');self.assertEqual(tail.read(),['新文件'])
 def test_redaction_and_escape(self):
  text=service_log.clean('\x1b[31mToken: super-secret https://example.com/a?token=secret\x1b[0m')
  self.assertNotIn('super-secret',text);self.assertNotIn('?token=',text);self.assertNotIn('\x1b',text)
 def test_prompt_durable_pinned_to_group_without_chat_side_effects(self):
  ident=operator_control.submit(self.db,self.cfg,'1','先了解最新话题，再决定要不要回复',self.now)
  row=self.db.execute('SELECT * FROM social_jobs').fetchone()
  self.assertEqual(row['source_group'],row['target_group']);self.assertEqual(row['event_key'],'operator:'+ident)
  self.assertEqual(self.db.execute('SELECT count(*) FROM events').fetchone()[0],0)
  items=operator_control.pending(self.db,'1');self.assertEqual(items[0]['id'],ident)
  operator_control.complete(self.db,items,'1','wait')
  self.assertEqual(self.db.execute('SELECT status FROM operator_prompts').fetchone()[0],'applied')
 def test_prompt_cannot_address_unconfigured_group(self):
  with self.assertRaises(ValueError):operator_control.submit(self.db,self.cfg,'2','发消息')
 def test_say_does_not_execute_slash_command(self):
  controller=console.Controller();controller.group='1'
  with patch('console.qq.load',return_value=(self.cfg,{},self.db)),patch('console.qq.send',return_value={'message_id':1}) as send:
   controller.execute('/say /bot stop');self.assertEqual(send.call_args.args[4],'/bot stop')
 def test_pending_prompt_budget_retains_remainder(self):
  for _ in range(3):operator_control.submit(self.db,self.cfg,'1','x'*6000,self.now)
  self.assertEqual(len(operator_control.pending(self.db,'1')),2)
 def test_clip_display_width(self):self.assertEqual(console.clip('中文abc',5),'中文a')

 def test_worker_reads_operator_prompt_from_application(self):
  self.cfg.update(mode='preview',interaction={'enabled':True,'quiet_seconds':0})
  event={'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':1,'time':self.now-10,'message':[{'type':'text','data':{'text':'普通聊天'}}]}
  workflow.ingest(self.db,event,self.cfg,self.now-10)
  ident=operator_control.submit(self.db,self.cfg,'1','只观察，别硬聊',self.now-9)
  with patch('social.load_model',return_value={'enabled':True,'api_key':'fake'}),patch('social.call_model',return_value=({'action':'wait','text':''},{})) as model:
   social.tick(self.db,self.cfg)
  self.assertEqual(model.call_args.args[1]['operator_prompts'][0]['id'],ident)
  self.assertEqual(self.db.execute('SELECT status FROM operator_prompts').fetchone()[0],'applied')

 def test_helper_does_not_duplicate_existing_connection(self):
  with patch('console.alive',return_value=None),patch('console.agent.onebot_call',return_value={'online':True}),patch('console.spawn') as spawn:
   self.assertIn('已有QQ连接',console.helper(self.cfg,{},'start'));spawn.assert_not_called()
 def test_helper_does_not_stop_external_instance(self):
  with patch('console.alive',return_value=None),patch('console.os.kill') as kill:
   with self.assertRaises(ValueError):console.helper(self.cfg,{},'stop')
   kill.assert_not_called()
 def test_helper_wrap_checks_account_before_termination(self):
  with patch('console.alive',return_value=None),patch('console.agent.onebot_call',side_effect=[{'online':True},{'user_id':100}]),patch('console.os.kill') as kill:
   with self.assertRaises(ValueError):console.helper(self.cfg,{},'wrap')
   kill.assert_not_called()
 def test_redact_json_tokens(self):self.assertNotIn('abc-secret',service_log.clean('{"token":"abc-secret"}'))

 def test_lite_is_default_with_full_override(self):
  from types import SimpleNamespace
  with patch('console.ROOT',self.root):
   self.assertTrue(console.lite_default(SimpleNamespace(full=False,lite=False)))
   self.assertFalse(console.lite_default(SimpleNamespace(full=True,lite=False)))
 def test_single_console_lock_released_on_close(self):
  with patch('console.ROOT',self.root):
   first=console.console_lock()
   try:
    with self.assertRaises(SystemExit):console.console_lock()
   finally:first.close()
   second=console.console_lock();second.close()
