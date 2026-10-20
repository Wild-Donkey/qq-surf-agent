import json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import onboarding,workflow
from memory import MarkdownMemory

class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cfg={'bot_id':'99','groups':[{'id':'1','name':'A'},{'id':'2','name':'B'}],'context_dir':str(self.root/'context'),'onboarding':{'enabled':True}}
        self.db=workflow.connect(':memory:');onboarding.ensure_schema(self.db);self.now=time.time()
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_discovery_distinguishes_existing_groups_from_new(self):
        path=self.root/'workflow.json';path.write_text(json.dumps(self.cfg))
        with patch('agent.onebot_call',side_effect=[{'user_id':99},[{'group_id':1,'group_name':'A'},{'group_id':2,'group_name':'B'},{'group_id':3,'group_name':'C'}]]):
            result=onboarding.discover(self.db,self.cfg,{},path,self.now)
        self.assertEqual(len(result['groups']),3)
        self.assertEqual(dict(self.db.execute('SELECT group_id,new_group FROM group_onboarding')),{'1':0,'2':0,'3':1})
    def test_scan_recognizes_known_account_distills_without_replaying_history(self):
        memory=MarkdownMemory(self.cfg['context_dir'])
        event={'message_type':'group','group_id':2,'user_id':7,'message_id':1,'time':self.now,'sender':{'nickname':'熟面孔'},'message':[{'type':'text','data':{'text':'聊技术'}}]}
        memory.observe(event,'99');memory.close()
        self.db.execute("INSERT INTO group_onboarding(group_id,created,status,new_group) VALUES ('1',?,'pending',1)",(self.now,));self.db.commit()
        event=dict(event,group_id=1,message_id=2)
        model={'enabled':True,'api_key':'test','model':'chosen'}
        with patch('agent.onebot_call',side_effect=[[{'user_id':7,'nickname':'熟面孔'}],{'messages':[event]}]),patch('social.load_model',return_value=model),patch('social.generate_text',return_value=(json.dumps({'summary':'经常讨论技术','style':'短句','topics':['技术']}),{'prompt_tokens':10,'completion_tokens':5})):
            onboarding.bootstrap(self.db,self.cfg,{},self.now)
        text=(self.root/'context/groups/1/group.md').read_text()
        self.assertIn('熟面孔',text);self.assertIn('经常讨论技术',text)
        self.assertEqual(self.db.execute('SELECT count(*) FROM social_jobs').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
        with patch('social.load_model',return_value=model):
            onboarding.greet(self.db,self.cfg,self.now);onboarding.greet(self.db,self.cfg,self.now+1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
        text=self.db.execute('SELECT text FROM candidates').fetchone()[0]
        self.assertIn('chosen',text)
        self.assertIn('https://github.com/Wild-Donkey/qq-surf-agent',text)
        self.assertIn('AI',text)
    def test_dormant_new_group_is_not_greeted(self):
        self.db.execute("INSERT INTO group_onboarding(group_id,created,status,new_group) VALUES ('1',?,'done',1)",(self.now,))
        self.db.execute('INSERT INTO group_activity VALUES (?,?)',('1',self.now-15*86400));self.db.commit()
        with patch('social.load_model',return_value={'enabled':True,'api_key':'test'}):onboarding.greet(self.db,self.cfg,self.now)
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
