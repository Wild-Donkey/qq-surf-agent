import json,tempfile,time,unittest
from pathlib import Path
from app import memory_store,context_memory,identities,archive

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
 def test_project_dedupes_retrace_and_archived_against_timeline(self):
  p={'decision_scope':{'group_id':'1'},'group_context':{'id':'1'},
    'recent_target_messages':[{'speaker':'7','text':'已经在时间线里','message_id':'2','event_key':'1:2','time':2}],
    'orientation_history':{'messages':[{'event_key':'1:2','text':'已经在时间线里'},{'event_key':'1:1','text':'回溯才有的旧消息'}],
     'coverage':{'available_messages':2,'included_messages':2}},
    'relevant_archived_messages':[{'message_id':'2','text':'重复的检索命中'},{'message_id':'9','text':'真正更早的记录'}]}
  result=context_memory.project(p)
  retrace=result['group_context']['retrace_input']['messages']
  self.assertEqual([r['event_key'] for r in retrace],['1:1'])
  self.assertEqual(result['group_context']['retrace_input']['coverage']['deduped_against_timeline'],1)
  records=result['group_context']['older_context']['original_records']
  self.assertEqual([r['message_id'] for r in records],['9'])
 def test_invalid_context_version_is_rejected(self):
  from app.context_contract import check
  with self.assertRaises(ValueError):check('WorkerContext',{'context_version':2,'bot_identity':{},'group_context':{},'conversation':{'timeline':[],'reference_evidence':[],'history_coverage':{}}})
 def test_fit_card_never_leaves_dangling_bracket_and_cleans_invisible(self):
  self.assertEqual(identities.fit_card('蔬菜布丁（有事打QQ电话联系）'),'蔬菜布丁')
  self.assertEqual(identities.fit_card('蔬菜布丁（有事）'),'蔬菜布丁（有事）')
  self.assertEqual(identities.clean_card('\u202d'),'')
 def test_worker_payload_renders_group_card_line_by_and_restores_at(self):
  with tempfile.TemporaryDirectory() as root:
   disk=archive.Archive(root)
   disk.record({'group_id':1,'user_id':7,'message_id':1,'time':100,'sender':{'card':'蔬菜布丁（有事打QQ电话联系）'},'message':[{'type':'text','data':{'text':'在吗'}}]})
   disk.record({'group_id':1,'user_id':123456789,'message_id':2,'time':101,'sender':{'card':'阿甲'},'message':[{'type':'text','data':{'text':'哈喽'}}]})
   disk.close()
   p={'_identity_root':root,'_identity_bot':'1550068162','decision_scope':{'group_id':'1'},
      'unread_messages':[{'speaker':'7','text':'@1550068162 @123456789 我哥跟你说话呢','mentions':['1550068162'],'message_id':'3','event_key':'1:3','time':102}],
      'group_context':{'name':'窝捕星'}}
   result=identities.worker_payload(p)
   item=result['conversation']['timeline'][0]
   self.assertTrue(item['line'].startswith('蔬菜布丁：'),item['line'])
   self.assertTrue(item['by'])
   self.assertIn('@Luna',item['line']);self.assertIn('@阿甲',item['line'])
   self.assertNotIn('1550068162',item['line']);self.assertNotIn('123456789',item['line'])
   self.assertNotIn('（',item['line'].split('：',1)[0])
 def test_worker_payload_disambiguates_identical_group_cards(self):
  with tempfile.TemporaryDirectory() as root:
   disk=archive.Archive(root)
   disk.record({'group_id':1,'user_id':7,'message_id':1,'time':100,'sender':{'card':'阿伟'},'message':[{'type':'text','data':{'text':'第一'}}]})
   disk.record({'group_id':1,'user_id':8,'message_id':2,'time':101,'sender':{'card':'阿伟'},'message':[{'type':'text','data':{'text':'第二'}}]})
   disk.close()
   p={'_identity_root':root,'decision_scope':{'group_id':'1'},
      'unread_messages':[{'speaker':'7','text':'第一','message_id':'1','event_key':'1:1','time':100},
                         {'speaker':'8','text':'第二','message_id':'2','event_key':'1:2','time':101}],
      'group_context':{}}
   result=identities.worker_payload(p)
   names=[item['line'].split('：',1)[0] for item in result['conversation']['timeline']]
   self.assertEqual(names[0],'阿伟');self.assertIn('阿伟·',names[1]);self.assertNotEqual(names[0],names[1])
 def test_worker_payload_reply_to_quote_keeps_author_line(self):
  with tempfile.TemporaryDirectory() as root:
   disk=archive.Archive(root)
   disk.record({'group_id':1,'user_id':7,'message_id':1,'time':100,'sender':{'card':'阿甲'},'message':[{'type':'text','data':{'text':'我哥说他周末来'}}]})
   disk.close()
   quote={'message_id':'1','event_key':'1:1','speaker':'7','nickname':'阿甲','time':100,'text':'我哥说他周末来','available':True}
   p={'_identity_root':root,'decision_scope':{'group_id':'1'},
      'unread_messages':[{'speaker':'8','nickname':'阿乙','text':'真的吗','mentions':[],'reply_to':quote,'message_id':'2','event_key':'1:2','time':101}],
      'group_context':{}}
   result=identities.worker_payload(p)
   item=result['conversation']['timeline'][0]
   self.assertTrue(item['line'].startswith('阿乙：'),item['line'])
   self.assertTrue(item['reply_to']['line'].startswith('阿甲：'),item['reply_to'])
   self.assertEqual(item['reply_to']['available'],True)
