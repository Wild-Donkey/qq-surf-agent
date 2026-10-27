"""chat_view, lite_console, lite_view, log_scroll, terminal_mouse regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import patch
from app import chat_view
import curses
import io
import tempfile
from unittest.mock import Mock
from app import lite_console
from app.lite_view import History
from app.log_scroll import Viewport
import collections
from app.terminal_mouse import Reader
from app.terminal_mouse import Mouse
from app.terminal_mouse import decode


class ChatViewTests(unittest.TestCase):
 def test_chat_format(self):
  row=chat_view.parse('10-21 14:02:05 10-21 14:02:05 [info] bot | 接收 <- 群聊 [武汉两平米棺材房(726856276)] [很长很长很长昵称(123456789)] 你好')
  self.assertEqual(row.text,'14:02:05 ← [武汉两平米] 很长很长很：你好')
  self.assertEqual(row.group,'726856276');self.assertNotIn('123456789',row.text);self.assertEqual(row.text.count('14:02:05'),1)
 def test_private_and_plain(self):
  row=chat_view.parse('10-21 14:02:05 10-21 14:02:05 [info] bot | 发送 -> 私聊 (123456789) 你好')
  self.assertNotIn('123456789',row.text)
  self.assertEqual(chat_view.parse('原生服务日志').text,'原生服务日志')
 def test_names_with_parentheses_and_math_preserved(self):
  row=chat_view.parse('10-21 14:02:05 [info] bot | 接收 <- 群聊 [群(测试)(12345)] [人(67890)] @朋友 (12345678) 计算(12345)')
  self.assertNotIn('(12345678)',row.text);self.assertIn('计算(12345)',row.text)
 def test_group_backgrounds_distinct_and_readable(self):
  class Avatars:
   def color(self,kind,ident):return (50,70,200) if kind=='group' else (200,30,80)
  with patch('curses.has_colors',return_value=True),patch('curses.start_color'),patch('curses.color_pair',side_effect=lambda n:n),patch('curses.init_pair') as init,patch.object(curses,'COLORS',256,create=True),patch.object(curses,'COLOR_PAIRS',256,create=True):
   styles=chat_view.Styles(Avatars())
   for group in ('1','2','3'):
    styles.attr(chat_view.Row('text',group,'7'))
    _,fg,bg=init.call_args.args
    self.assertGreaterEqual(chat_view.contrast(chat_view.rgb(fg),chat_view.rgb(bg)),4.5)
   self.assertEqual(len(set(styles.backgrounds.values())),3)


class LiteTests(unittest.TestCase):
 def test_only_chat_no_native_warnings_or_worker_output(self):
  lines=['10-21 14:02:05 10-21 14:02:05 [info] bot | 接收 <- 群聊 [武汉两平米棺材房(12345)] [测试名字(67890)] 你好',
         '10-21 14:02:05 worker processing group=12345','10-21 14:02:05 [warn] bot | 查询失败']
  self.assertEqual(lite_console.chat_lines(lines),['14:02:05 ← [武汉两平米] 测试名字：你好'])
 def test_plain_loop_commands_includes_bot_log_without_color(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'runtime/logs').mkdir(parents=True)
   (root/'runtime/logs/qq-helper.log').write_text('10-21 14:02:05 [info] bot | 接收 <- 群聊 [群(12345)] [人(67890)] hello\n')
   (root/'runtime/logs/surf-bot.log').write_text('10-21 14:02:06 worker processing group=12345\n')
   out=io.StringIO();controller=Mock();controller.execute.return_value='已选择群'
   with patch('app.lite_console.ROOT',root),patch('app.avatar_palette.AvatarPalette',side_effect=AssertionError('must not load avatar colors')):
    lite_console.run(controller,Mock(side_effect=['/use 群','/help','/quit']),out)
   self.assertNotIn('\x1b',out.getvalue());self.assertIn('[worker]',out.getvalue());self.assertIn('处理',out.getvalue());self.assertIn('hello',out.getvalue())
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


class LiteScrollTests(unittest.TestCase):
 def test_bidirectional_scroll_and_new_messages_do_not_move_history(self):
  h=History();h.append([str(n) for n in range(20)])
  self.assertEqual([r[0] for r in h.visible(80,4)],['16','17','18','19'])
  h.scroll(5);before=h.visible(80,4);h.append(['20','21'])
  self.assertEqual(h.visible(80,4),before);self.assertEqual(h.new,2)
  h.scroll(-3);self.assertNotEqual(h.visible(80,4),before)
  h.live();self.assertEqual([r[0] for r in h.visible(80,4)],['18','19','20','21'])
 def test_long_lines_scroll_by_visual_rows(self):
  h=History();h.append(['x'*15,'tail']);h.visible(5,2);h.scroll(1)
  self.assertEqual([r[0] for r in h.visible(5,2)],['xxxxx','xxxxx'])


class ScrollTests(unittest.TestCase):
 def test_scrolls_display_rows_not_entire_long_message(self):
  v=Viewport();lines=['abcdefghijkl','next']
  self.assertEqual([r[0] for r in v.visible(lines,4,2)],['ijkl','next'])
  v.scroll(1);self.assertEqual([r[0] for r in v.visible(lines,4,2)],['efgh','ijkl'])
  v.scroll(100);self.assertEqual([r[0] for r in v.visible(lines,4,2)],['abcd','efgh'])
 def test_new_messages_do_not_move_historical_view(self):
  v=Viewport();lines=['one','two','three','four'];v.visible(lines,20,2);v.scroll(1)
  before=v.visible(lines,20,2);v.arrived(['five','six']);lines+=['five','six']
  self.assertEqual(v.visible(lines,20,2),before)
  v.live();self.assertEqual([r[0] for r in v.visible(lines,20,2)],['five','six'])
 def test_at_bottom_continues_following(self):
  v=Viewport();v.visible(['old'],10,1);v.arrived(['new'])
  self.assertEqual(v.visible(['old','new'],10,1)[0][0],'new')
 def test_unicode_row_width(self):
  v=Viewport();self.assertEqual([r[0] for r in v.visible(['中文abc'],4,4)],['中文','abc'])


class Screen:
 def __init__(self,chars):self.chars=collections.deque(chars)
 def get_wch(self):
  if not self.chars:raise curses.error()
  return self.chars.popleft()


class MouseTests(unittest.TestCase):
 def test_both_wheel_directions_without_button5_constant(self):
  self.assertEqual(decode('\x1b[<64;20;5M'),Mouse(19,4,3,False))
  self.assertEqual(decode('\x1b[<65;20;5M'),Mouse(19,4,-3,False))
 def test_fragmented_packet_never_becomes_typed_text(self):
  screen=Screen('\x1b[<65;');reader=Reader();self.assertIsNone(reader.get(screen))
  screen.chars.extend('20;5M');self.assertEqual(reader.get(screen),Mouse(19,4,-3,False))
 def test_native_keys_and_unicode_preserved(self):
  screen=Screen([curses.KEY_DOWN,'中']);reader=Reader()
  self.assertEqual(reader.get(screen),curses.KEY_DOWN);self.assertEqual(reader.get(screen),'中')
 def test_modifiers_and_release(self):
  self.assertEqual(decode('\x1b[<81;3;4M').delta,-3)
  self.assertEqual(decode('\x1b[<65;3;4m').delta,0)
 def test_unsupported_sequence_not_typed(self):
  reader=Reader();screen=Screen('\x1b[999~x');self.assertIsNone(reader.get(screen));self.assertEqual(reader.get(screen),'x')


if __name__ == '__main__':
    unittest.main()


class UnifiedFeedTests(unittest.TestCase):
 def test_chronological_qq_app_and_readable_understanding(self):
  import json
  from app.terminal_feed import Feed
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);logs=root/'runtime/logs';logs.mkdir(parents=True)
   (root/'workflow.json').write_text(json.dumps({'groups':[{'id':12345,'name':'测试群'}]}))
   (logs/'qq-helper.log').write_text('10-21 14:02:05 [info] bot | 接收 <- 群聊 [测试群(12345)] [小明(67890)] hello\n10-21 14:02:07 [warn] bot | QQ warning\n')
   notes={'summary':'话题在讨论猫','images':[{'reference':'image1','description':'猫在纸箱上'}],'uncertainties':['不知道是谁家的猫']}
   (logs/'surf-bot.log').write_text('10-21 14:02:06 worker understanding group=12345 request_id=abc observations='+json.dumps(notes,ensure_ascii=False)+'\n')
   feed=Feed(root);rows=feed.read();text='\n'.join(rows)
   self.assertIn('hello',rows[0]);self.assertIn('[worker][测试群] 局面理解',text)
   self.assertIn('图片 image1：猫在纸箱上',text);self.assertIn('待确认：不知道是谁家的猫',text)
   self.assertIn('[QQ]',rows[-1]);self.assertEqual(feed.read(),[])
   with (logs/'surf-bot.log').open('a') as f:f.write('10-21 14:02:08 model ready\n')
   self.assertIn('[supervisor] model ready',feed.read()[0])
 def test_full_console_single_pane(self):
  from app import console
  screen=Mock();screen.getmaxyx.return_value=(30,120)
  with tempfile.TemporaryDirectory() as tmp,patch('app.console.ROOT',Path(tmp)),patch('app.console.Controller'),patch('app.console.avatar_palette.AvatarPalette'),patch('app.console.chat_view.Styles'),patch('app.console.curses.curs_set'),patch('app.console.curses.mousemask'),patch('app.console.sys.stdout',io.StringIO()),patch('app.console.terminal_mouse.Reader') as reader:
   reader.return_value.get.side_effect=[curses.KEY_F2,None,curses.KEY_F2]+list('/quit\n')
   console.run(screen)
  shown=[str(call.args[2]) for call in screen.addstr.call_args_list]
  self.assertTrue(any('QQ / 应用 / 理解' in line for line in shown))
  self.assertTrue(any('复制模式：' in line for line in shown))
  self.assertNotIn('│',shown);self.assertFalse(any('surf bot / 操作结果' in line for line in shown))


class RetraceDisplayTests(unittest.TestCase):
 def test_orientation_notes_render_in_unified_feed(self):
  import json
  from app.terminal_feed import format_lines
  note={'topics':[{'text':'讨论运行方式','evidence_event_keys':['1:1']}],'referents':[{'expression':'那台','interpretation':'前文的电脑','certainty':'tentative','evidence_event_keys':['1:1']}],'open_interactions':[]}
  rows=format_lines(['10-21 19:00:00 group retrace complete group=1 referents=1 notes='+json.dumps(note,ensure_ascii=False)],'应用',{'1':'测试群'})
  self.assertIn('[worker][测试群] 回溯完成',rows[0]);self.assertIn('那台 → 前文的电脑（推测）','\n'.join(rows))
