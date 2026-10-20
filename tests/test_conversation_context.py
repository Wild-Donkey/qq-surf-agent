import sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workflow,social
from memory import MarkdownMemory

class ConversationContextTests(unittest.TestCase):
    def test_state_and_relevant_old_window_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=time.time();memory=MarkdownMemory(tmp)
            memory.remember_window('1',[{'event_key':'1:1','created':now-100}],
                                   '本群约定称呼Luna，正在讨论部署。','群友把bot称呼改成Luna，另有一只猫叫阿黄。')
            memory.remember_window('1',[{'event_key':'1:2','created':now}],
                                   '称呼Luna；当前在讨论晚饭。','现在在聊晚饭吃什么。')
            memory.close();memory=MarkdownMemory(tmp)
            self.assertIn('Luna',memory.conversation_state('1'))
            matches=memory.recall_episodes('1','那只猫阿黄叫什么')
            self.assertTrue(matches);self.assertIn('阿黄',matches[0]['summary'])
            self.assertEqual(memory.recall_episodes('2','阿黄'),[])
            self.assertIn('持续对话状态',(Path(tmp)/'groups/1/group.md').read_text())
            memory.close()
    def test_old_job_uses_latest_larger_context_with_speakers_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'bot_id':'99','groups':[{'id':'1','name':'A'}],'context_dir':tmp,'context_messages':100,
                 'context_char_budget':24000,'interaction':{'enabled':True}}
            db=workflow.connect(':memory:');now=time.time()
            for index in range(25):
                event={'post_type':'message','message_type':'group','group_id':1,'user_id':7+index%2,
                       'message_id':index+1,'sender':{'nickname':'群友'+str(index%2)},
                       'message':[{'type':'text','data':{'text':'历史消息'+str(index)}}]}
                workflow.ingest(db,event,cfg,now-50+index)
            event={'post_type':'message','message_type':'group','group_id':1,'user_id':8,'message_id':26,
                   'sender':{'nickname':'群友1'},'message':[{'type':'reply','data':{'id':23}},{'type':'text','data':{'text':'最新问题，接着上面说'}}]}
            workflow.ingest(db,event,cfg,now)
            job=db.execute('SELECT * FROM social_jobs ORDER BY created LIMIT 1').fetchone()
            memory=MarkdownMemory(tmp);payload,_=social.choose(db,cfg,memory,job)
            recent=payload['recent_target_messages']
            self.assertEqual(len(recent),26)
            self.assertEqual(recent[-1]['text'],'最新问题，接着上面说')
            self.assertEqual(recent[-1]['speaker'],'8')
            self.assertEqual(recent[-1]['reply_to']['text'],'历史消息22')
            memory.close();db.close()
