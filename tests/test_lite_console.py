import io,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import lite_console
class LiteTests(unittest.TestCase):
 def test_only_chat_no_native_warnings_or_worker_output(self):
  lines=['10-21 14:02:05 10-21 14:02:05 [info] bot | 接收 <- 群聊 [武汉两平米棺材房(12345)] [测试名字(67890)] 你好',
         '10-21 14:02:05 worker processing group=12345','10-21 14:02:05 [warn] bot | 查询失败']
  self.assertEqual(lite_console.chat_lines(lines),['14:02:05 ← [武汉两平米] 测试名字：你好'])
 def test_plain_loop_commands_no_color_or_bot_log(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'runtime/logs').mkdir(parents=True)
   (root/'runtime/logs/qq-helper.log').write_text('10-21 14:02:05 [info] bot | 接收 <- 群聊 [群(12345)] [人(67890)] hello\n')
   (root/'runtime/logs/surf-bot.log').write_text('must-never-show')
   out=io.StringIO();controller=Mock();controller.execute.return_value='已选择群'
   with patch('lite_console.ROOT',root),patch('avatar_palette.AvatarPalette',side_effect=AssertionError('must not load avatar colors')):
    lite_console.run(controller,Mock(side_effect=['/use 群','/help','/quit']),out)
   self.assertNotIn('\x1b',out.getvalue());self.assertNotIn('must-never-show',out.getvalue());self.assertIn('hello',out.getvalue())
   controller.execute.assert_called_once_with('/use 群')
 def test_readline_completion_uses_command_prefix(self):
  read=Mock();read.__doc__='readline';read.get_line_buffer.return_value='/wor';read.get_endidx.return_value=4
  lite_console.setup_completion(read);callback=read.set_completer.call_args.args[0]
  self.assertEqual(callback('',0),'/worker ');self.assertIsNone(callback('',1))

 def test_async_log_erases_prompt_without_blank_prompt_line(self):
  import collections
  out=io.StringIO();out.isatty=lambda:True
  lite_console.print_chat_batch(out,collections.deque(['聊天消息']),True)
  self.assertEqual(out.getvalue(),'\r  \r聊天消息\n> ')
