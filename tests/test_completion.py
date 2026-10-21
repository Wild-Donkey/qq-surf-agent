import time,unittest
from unittest.mock import patch
import completion,console,qq,workflow
class CompletionTests(unittest.TestCase):
 def test_unique_command_and_service_action(self):
  c=completion.Completer()
  self.assertEqual(c.tab('/wor',4,[]),('/worker ',8))
  self.assertEqual(c.tab('/bot re',7,[]),('/bot restart ',13))
 def test_ambiguous_candidates_cycle(self):
  c=completion.Completer();self.assertEqual(c.tab('/s',2,[]),('/s',2))
  one=c.tab('/s',2,[]);two=c.tab(*one,[])
  self.assertNotEqual(one,two);self.assertTrue(one[0].startswith('/s'))
 def test_group_name_and_id(self):
  groups=[{'id':123,'name':'武汉棺材房'},{'id':456,'name':'别的群','enabled':False}]
  c=completion.Completer();self.assertEqual(c.tab('/use 棺材',7,groups)[0],'/use 武汉棺材房 ')
  self.assertEqual(len(completion.candidates('/use ',groups)),1)
 def test_plain_text_not_rewritten(self):
  c=completion.Completer();self.assertEqual(c.tab('我要说一句',5,[]),('我要说一句',5))
 def test_mark_sent_once_in_actual_payload(self):
  db=workflow.connect(':memory:')
  with db:db.execute('INSERT INTO group_activity VALUES (?,?)',('1',time.time()))
  try:
   with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':123}]) as api:
    qq.send(db,{'bot_id':99},{},'1','我来接话')
   self.assertEqual(api.call_args.args[2]['message'][0]['data']['text'],'【人工接管】 我来接话')
   self.assertEqual(qq.manual_text('【人工接管】 已标记'),'【人工接管】 已标记')
  finally:db.close()
 def test_wrap_unicode_columns(self):
  self.assertEqual(console.wrap('中文abc',4),['中文','abc'])

 def test_duplicate_names_show_names_and_resolve_exactly(self):
  groups=[{'id':1,'name':'群'},{'id':2,'name':'群'},{'id':3,'name':'群聊'}]
  options=completion.candidates('/use ',groups)
  self.assertEqual(options[0][0],'/use 群 [1] ')
  self.assertEqual(qq.resolve_group('群 [1]',{'groups':groups})['id'],1)
  self.assertEqual(qq.resolve_group('群聊',{'groups':groups})['id'],3)
