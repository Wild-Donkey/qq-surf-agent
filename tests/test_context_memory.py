import json,tempfile,time,unittest
from pathlib import Path
from app import memory_store, context_memory, identities

class ContextMemoryTests(unittest.TestCase):
 def test_proactive_topic_recall_separates_uncertainty_and_boundaries(self):
  with tempfile.TemporaryDirectory() as root:
   store=memory_store.MemoryStore(root);doc=store.load('person','7');now=time.time()
   def fact(text,confidence=1):return {'text':text,'confidence':confidence,'evidence':['1:1'],'first_seen':now,'last_seen':now,'source':'worker','status':'supported' if confidence>=.85 else 'tentative'}
   doc['portrait']['interests']=[fact('喜欢红黑树算法'),fact('爱看足球')]
   doc['portrait']['boundaries']=[fact('不喜欢别人连戳')]
   doc['portrait']['uncertainties']=[fact('红黑树课程是否学过',.5)]
   store.commit([doc],'test');store.close()
   result=context_memory.retrieve({'context_dir':root},'1',{'unread_messages':[{'speaker':'7','text':'红黑树怎么旋转'}]})
   self.assertEqual(result['facts'][0]['text'],'喜欢红黑树算法');self.assertEqual(len(result['facts']),1)
   self.assertEqual(result['uncertainties'][0]['status'],'tentative');self.assertEqual(result['boundaries'][0]['text'],'不喜欢别人连戳')
 def test_single_timeline_no_duplicate_personal_transcripts(self):
  with tempfile.TemporaryDirectory() as root:
   p={'_identity_root':root,'decision_scope':{'group_id':'1'},'unread_messages':[{'speaker':'7','nickname':'阿甲','text':'当前对话','message_id':'3','event_key':'1:3','time':3}],
    'recent_target_messages':[{'speaker':'7','nickname':'阿甲','text':'当前对话','message_id':'3','event_key':'1:3','time':3}],
    'last_two_hours':{'messages':[{'user_id':'7','nickname':'卡片','text':'旧对话','message_id':'2','created':2},{'user_id':'7','text':'当前对话','message_id':'3','created':3}]},
    'member_memories':[{'user_id':'7','markdown':'重复的逐句记录'}],'group_context':{'name':'群'}}
   result=identities.worker_payload(p)
   self.assertEqual(len(result['conversation']['timeline']),2);self.assertTrue(result['conversation']['timeline'][1]['unread'])
   self.assertNotIn('unread_messages',result);self.assertNotIn('last_two_hours',result);self.assertNotIn('member_memories',result)
   self.assertNotIn('memory',p['group_context'])
 def test_compaction_preserves_evidence_and_creates_backup(self):
  with tempfile.TemporaryDirectory() as root:
   store=memory_store.MemoryStore(root);doc=store.load('person','7');now=time.time()
   base={'text':'爱研究算法','confidence':.9,'evidence':['1:1'],'first_seen':now,'last_seen':now,'source':'worker','status':'supported'}
   doc['portrait']['interests']=[base,{**base,'evidence':['1:2']}];store.commit([doc],'test');store.close()
   result=context_memory.compact(root);self.assertEqual(result['duplicates'],1);self.assertTrue(Path(result['backup']).exists())
   store=memory_store.MemoryStore(root)
   self.assertEqual(store.load('person','7')['portrait']['interests'][0]['evidence'],['1:1','1:2']);store.close()
 def test_invalid_context_version_is_rejected(self):
  from context_contract import check
  with self.assertRaises(ValueError):check('WorkerContext',{'context_version':2,'bot_identity':{},'group_context':{},'conversation':{'timeline':[],'reference_evidence':[],'history_coverage':{}}})
