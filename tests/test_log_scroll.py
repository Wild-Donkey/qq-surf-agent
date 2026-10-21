import unittest
from log_scroll import Viewport
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
