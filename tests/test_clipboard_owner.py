import io,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import qq,workflow,operator_control,social,console,clipboard_image
from memory import MarkdownMemory
class ClipboardOwnerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=workflow.connect(':memory:');self.now=time.time()
  self.cfg={'bot_id':'99','groups':[{'id':'1','name':'群'}],'alerts':{'owner_qq':'7'},'context_dir':self.tmp.name,'interaction':{'enabled':True}}
  with self.db:self.db.execute('INSERT INTO group_activity VALUES (?,?)',('1',self.now))
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def png(self):
  out=io.BytesIO();Image.new('RGB',(4,4),'blue').save(out,format='PNG');return out.getvalue()
 def test_image_is_sent_to_selected_group_without_pause(self):
  with patch('qq.ROOT',Path(self.tmp.name)),patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':8}]) as api:
   qq.send_image(self.db,self.cfg,{},'1',self.png())
  payload=api.call_args.args[2];self.assertEqual(payload['group_id'],1)
  self.assertTrue(payload['message'][1]['data']['file'].startswith('base64://'))
  self.assertEqual(self.db.execute('SELECT count(*) FROM operator_pause').fetchone()[0],0)
  self.assertEqual(operator_control.owner_for(self.db,self.cfg,'1',8),'7')
  self.assertEqual(self.db.execute('SELECT user_id FROM social_jobs').fetchone()[0],'7')
  self.assertEqual(self.db.execute('SELECT trigger_images FROM social_jobs').fetchone()[0],1)
 def test_manual_owner_identity_uses_receipts_not_text(self):
  with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':8}]):qq.send(self.db,self.cfg,{},'1','我来接话')
  self.assertEqual(operator_control.owner_for(self.db,self.cfg,'1','bot:8'),'7')
  self.assertIsNone(operator_control.owner_for(self.db,self.cfg,'2','bot:8'))
  self.assertIsNone(operator_control.owner_for(self.db,self.cfg,'1','someone-else'))
  memory=MarkdownMemory(self.tmp.name)
  try:
   payload,_=social.choose(self.db,self.cfg,memory,self.db.execute('SELECT * FROM social_jobs').fetchone())
   row=payload['recent_target_messages'][-1];self.assertEqual(row['author_role'],'bot_owner');self.assertEqual(row['speaker'],'7')
  finally:memory.close()
 def test_clipboard_no_image_never_sends(self):
  controller=console.Controller();controller.group='1'
  with patch('console.qq.load',return_value=(self.cfg,{},self.db)),patch('clipboard_image.read_png',side_effect=ValueError('剪贴板没有图片')),patch('qq.send_image') as send:
   with self.assertRaises(ValueError):controller.execute('/paste')
   send.assert_not_called()
 def test_clipboard_text_is_not_executed(self):
  with patch('clipboard_image.subprocess.run') as run:
   run.return_value.returncode=1
   with self.assertRaises(ValueError):clipboard_image.read_png()
   self.assertEqual(run.call_args.args[0][0],'osascript');self.assertNotIn('shell',run.call_args.kwargs)
