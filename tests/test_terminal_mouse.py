import collections,curses,unittest
from terminal_mouse import Reader,Mouse,decode
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
