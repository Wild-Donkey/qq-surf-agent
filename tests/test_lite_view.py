import unittest
from lite_view import History
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
