import sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workflow,participants

class ParticipantTests(unittest.TestCase):
    def setUp(self):
        self.db=workflow.connect(':memory:')
        self.cfg={'bot_id':'99','groups':[{'id':'1','name':'A'}],
                  'human_presence':{'enabled':True,'known_humans':['7'],'known_bots':['8'],'timeout_seconds':300}}
    def tearDown(self):self.db.close()
    def observe(self,user,stamp):
        event={'group_id':1,'user_id':user,'message_id':str(user)+':'+str(stamp),'time':stamp}
        with self.db:participants.observe(self.db,event,self.cfg,'hello',stamp)
    def test_bot_and_unknown_never_extend_human_window(self):
        self.observe(7,1000);self.observe(8,1299);self.observe(9,1299)
        self.assertTrue(participants.humans_recent(self.db,'1',self.cfg,1299))
        self.assertFalse(participants.humans_recent(self.db,'1',self.cfg,1300))
        self.observe(8,1500)
        self.assertFalse(participants.humans_recent(self.db,'1',self.cfg,1501))
        self.observe(7,1502)
        self.assertTrue(participants.humans_recent(self.db,'1',self.cfg,1502))
    def test_reclassification_invalidates_past_human_activity(self):
        self.observe(9,1000);batch=[{'user_id':'9'}]
        with self.db:participants.apply_judgments(self.db,{'speakers':[{'id':'9','kind':'human','confidence':0.9}]},batch,self.cfg,1001)
        self.assertTrue(participants.humans_recent(self.db,'1',self.cfg,1001))
        with self.db:participants.apply_judgments(self.db,{'speakers':[{'id':'9','kind':'bot','confidence':0.95}]},batch,self.cfg,1002)
        self.assertFalse(participants.humans_recent(self.db,'1',self.cfg,1002))
    def test_unseen_ids_low_confidence_and_config_override_cannot_open_gate(self):
        self.observe(8,1000);self.observe(9,1000)
        with self.db:participants.apply_judgments(self.db,{'speakers':[{'id':'8','kind':'human','confidence':1},{'id':'9','kind':'human','confidence':0.5},{'id':'123','kind':'human','confidence':1}]},[{'user_id':'8'},{'user_id':'9'}],self.cfg,1001)
        self.assertFalse(participants.humans_recent(self.db,'1',self.cfg,1001))
        self.assertFalse(self.db.execute("SELECT 1 FROM speaker_profiles WHERE user_id='123'").fetchone())
