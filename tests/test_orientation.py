import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from app import archive,orientation,application,sessions,workflow,social,identities

class OrientationTests(unittest.TestCase):
 def test_archive_group_cutoff_and_evidence_validation(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root};disk=archive.Archive(root)
   for group,mid,when,text in ((1,1,100,'甲说旧电脑在线'),(1,2,101,'乙引用前文'),(2,3,100,'不能串群'),(1,4,200,'未来消息')):
    disk.record({'group_id':group,'user_id':7,'message_id':mid,'time':when,'message':[{'type':'text','data':{'text':text}}]})
   disk.add_visual('1','1','旧电脑截图');disk.close()
   history=orientation.prepare(cfg,'1',150,[])
   self.assertEqual([r['event_key'] for r in history['messages']],['1:1','1:2'])
   self.assertEqual(history['messages'][0]['visual_notes'],['旧电脑截图'])
   payload={'orientation_required':True,'orientation_history':history,'group_context':{}}
   args={'topics':[{'text':'本地部署','evidence_event_keys':['1:1']}],'referents':[{'expression':'那台','interpretation':'甲提到的旧电脑','evidence_event_keys':['1:1'],'certainty':'confirmed'}],'open_interactions':[{'text':'等待接话','evidence_event_keys':['1:2']}]}
   saved=orientation.save(cfg,'1',payload,args)
   self.assertFalse(payload['orientation_required']);self.assertNotIn('orientation_history',payload)
   self.assertEqual(payload['group_context']['orientation'],saved['orientation'])
   self.assertTrue((Path(root)/'groups/1/orientation.json').exists())
   payload.update(orientation_required=True,orientation_history=history);args['referents'][0]['evidence_event_keys']=['2:3']
   saved=orientation.save(cfg,'1',payload,args)
   self.assertEqual(saved['orientation']['referents'][0]['evidence_event_keys'],[])
   self.assertEqual(saved['orientation']['referents'][0]['certainty'],'tentative')
 def test_request_retrace_prepares_history_instead_of_replying(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root};payload={'recent_target_messages':[{'event_key':'1:1','speaker':'7','text':'那台机器'}]}
   result=application.read(None,cfg,'1',payload,{'name':'retrace_context','arguments':{'query':''}})
   self.assertTrue(result['prepared']);self.assertTrue(payload['orientation_required'])
   self.assertEqual(payload['orientation_history']['messages'][0]['text'],'那台机器')
   self.assertNotIn('messages',result)
 def test_new_session_requires_preflight_and_then_can_reply(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'A'}],'context_dir':root,'interaction':{'enabled':True,'quiet_seconds':0,'max_wait_seconds':20,'max_qps':1000,'orientation_enabled':True}}
   db=workflow.connect(':memory:');now=time.time()
   workflow.ingest(db,{'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':1,'time':now-10,'message':[{'type':'text','data':{'text':'电脑一直开着'}}]},cfg,now-10)
   def infer(model,payload):
    if payload.get('orientation_required'):
     return {'action':'read','call':{'name':'set_group_context','arguments':{'topics':['电脑运行'],'referents':[],'open_interactions':[]}},'text':''},{}
    self.assertIn('orientation',payload['group_context'])
    return {'action':'reply','text':'那散热得跟上'},{}
   with patch('app.social.load_model',return_value={'enabled':True,'api_key':'fake'}),patch('app.social.call_model',side_effect=infer) as call:
    social.tick(db,cfg)
   self.assertEqual(call.call_count,2);self.assertEqual(db.execute('select count(*) from deliveries').fetchone()[0],1)
   db.close()
 def test_prepare_carries_mentions_and_resolved_reply_to(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root};disk=archive.Archive(root)
   disk.record({'group_id':1,'user_id':7,'message_id':1,'time':100,'sender':{'card':'阿甲'},'message':[{'type':'text','data':{'text':'甲说旧电脑在线'}}]})
   disk.record({'group_id':1,'user_id':8,'message_id':2,'time':101,'sender':{'card':'阿乙'},'message':[{'type':'text','data':{'text':'引用前文'}},{'type':'reply','data':{'id':1}},{'type':'at','data':{'qq':'7'}}]})
   disk.record({'group_id':1,'user_id':8,'message_id':3,'time':102,'sender':{'card':'阿乙'},'message':[{'type':'text','data':{'text':'引用缺失'}},{'type':'reply','data':{'id':99}}]})
   disk.close()
   history=orientation.prepare(cfg,'1',150,[])
   bymid={m['event_key']:m for m in history['messages']}
   self.assertEqual(bymid['1:2']['mentions'],['7'])
   quote=bymid['1:2']['reply_to']
   self.assertEqual(quote['speaker'],'7');self.assertEqual(quote['nickname'],'阿甲')
   self.assertIn('旧电脑',quote['text']);self.assertTrue(quote['available'])
   missing=bymid['1:3']['reply_to']
   self.assertFalse(missing['available']);self.assertEqual(missing['speaker'],'unknown')
 def test_retrace_search_resolves_reply_to_quote(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'context_dir':root};disk=archive.Archive(root)
   disk.record({'group_id':1,'user_id':7,'message_id':1,'time':100,'sender':{'card':'阿甲'},'message':[{'type':'text','data':{'text':'甲说旧电脑在线'}}]})
   disk.record({'group_id':1,'user_id':8,'message_id':5,'time':101,'sender':{'card':'阿乙'},'message':[{'type':'text','data':{'text':'旧电脑现在还在线吗'}},{'type':'reply','data':{'id':1}}]})
   disk.close()
   payload={'recent_target_messages':[]}
   application.read(None,cfg,'1',payload,{'name':'retrace_context','arguments':{'query':'旧电脑'}})
   quoted=[m for m in payload['orientation_history']['messages'] if isinstance(m.get('reply_to'),dict) and m['reply_to'].get('available')]
   self.assertTrue(quoted);self.assertEqual(quoted[0]['reply_to']['speaker'],'7')
   self.assertIn('旧电脑',quoted[0]['reply_to']['text'])
 def test_reply_is_blocked_if_preflight_was_skipped(self):
  with tempfile.TemporaryDirectory() as root:
   cfg={'bot_id':'99','mode':'live','groups':[{'id':'1','name':'A'}],'context_dir':root,'interaction':{'enabled':True,'quiet_seconds':0,'orientation_enabled':True}}
   db=workflow.connect(':memory:');now=time.time()
   workflow.ingest(db,{'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':1,'message':[{'type':'text','data':{'text':'不明白的对话'}}]},cfg,now-10)
   with patch('app.social.load_model',return_value={'enabled':True,'api_key':'fake'}),patch('app.social.call_model',return_value=({'action':'reply','text':'随口猜一句'},{})):social.tick(db,cfg)
   self.assertEqual(db.execute('select count(*) from deliveries').fetchone()[0],0);db.close()
