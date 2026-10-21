import tempfile,unittest,time
from pathlib import Path
import archive,workflow,conversation_refs,social
from memory import MarkdownMemory

class ReferenceTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=workflow.connect(':memory:');self.cfg={'context_dir':self.tmp.name,'bot_id':'99'}
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def record(self,g,mid,user,name,text,stamp):
  disk=archive.Archive(self.root)
  try:disk.record({'group_id':g,'message_id':mid,'user_id':user,'sender':{'nickname':name},'time':stamp,'message':[{'type':'text','data':{'text':text}}]})
  finally:disk.close()
 def test_quoted_self_retains_author_and_retrieves_original_member(self):
  now=time.time();self.record(1,1,7,'Kyoko','邻居洗衣服洗到凌晨两点',now-10000);self.record(1,2,99,'Luna','有人洗衣服洗到凌晨两点',now-5000)
  memory=MarkdownMemory(self.root)
  try:view=social.message_view(self.db,memory,'1','那个人是谁',0,'8','2')
  finally:memory.close()
  self.assertEqual(view['reply_to']['speaker'],'99');self.assertEqual(view['reply_to']['nickname'],'Luna')
  payload={'unread_messages':[view],'recent_target_messages':[]};users=conversation_refs.enrich(self.cfg,'1',payload)
  self.assertIn('7',users);self.assertEqual(payload['reference_evidence'][0]['nickname'],'Kyoko');self.assertEqual(view['reply_to']['author_role'],'self')
 def test_quote_and_related_history_never_cross_groups(self):
  self.record(2,2,7,'foreign','洗衣服洗到凌晨两点',time.time())
  ref=conversation_refs.quote(self.db,self.root,'1','2');self.assertFalse(ref['available']);self.assertEqual(ref['speaker'],'unknown')
 def test_mentioned_and_quoted_members_are_selected_for_memory(self):
  self.record(1,3,7,'person','a quoted message with detail',time.time())
  view={'mentions':['8','all'],'reply_to':conversation_refs.quote(self.db,self.root,'1','3')}
  users=conversation_refs.enrich(self.cfg,'1',{'unread_messages':[view]})
  self.assertTrue({'7','8'}<=users)
