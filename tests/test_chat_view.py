import unittest
from unittest.mock import patch
import chat_view,curses
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
