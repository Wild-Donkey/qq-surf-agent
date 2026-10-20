import tempfile,time,unittest
from archive import Archive
class ArchiveTests(unittest.TestCase):
 def test_permanent_restart_group_search_and_window(self):
  with tempfile.TemporaryDirectory() as root:
   a=Archive(root)
   e={'group_id':1,'message_id':2,'user_id':3,'time':time.time()-200*86400,'message':[{'type':'text','data':{'text':'阿黄猫在武汉'}}]}
   a.record(e);a.close();a=Archive(root)
   self.assertEqual(a.get('1','2')['text'],'阿黄猫在武汉')
   self.assertTrue(a.search('1','阿黄猫'));self.assertEqual(a.search('2','阿黄猫'),[])
   self.assertEqual(a.window('1',0)['total_messages'],1)
   a.add_visual('1','2','蓝色猫窝')
   self.assertTrue(a.search('1','猫窝'));a.close()
 def test_backfill_is_enriched_by_original_mentions(self):
  with tempfile.TemporaryDirectory() as root:
   a=Archive(root);e={'group_id':1,'message_id':2,'user_id':'unknown','message':[{'type':'text','data':{'text':'清理上下文'}}],'archive_origin':'working_cache_backfill'}
   a.record(e);e.pop('archive_origin');e['user_id']='3';e['message'].append({'type':'at','data':{'qq':'9'}});a.record(e)
   import json
   saved=a.get('1','2');self.assertEqual(saved['user_id'],'3');self.assertEqual(json.loads(saved['mentions']),['9']);a.close()
