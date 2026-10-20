import hashlib
import hmac
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workflow as w


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.db=w.connect(':memory:')
        self.db.executemany('INSERT INTO group_activity VALUES (?,?)', [('1',1000),('2',1000),('3',1000)])
        self.db.commit()
        self.cfg={'bot_id':'99','mode':'preview','hold_seconds':0,
                  'groups':[{'id':1,'name':'A'},{'id':2,'name':'B'},{'id':3,'name':'C'}]}
    def tearDown(self): self.db.close()
    def event(self, mid=1, group=1, text='今日整活 https://example.com/post', images=False):
        msg=[{'type':'text','data':{'text':text}}]
        if images: msg.append({'type':'image','data':{'url':'https://example.com/a.png?token=1','file':'abc.jpg'}})
        return {'post_type':'message','message_type':'group','group_id':group,'user_id':7,'message_id':mid,'time':1000,'message':msg}
    def test_routes_and_seen_suppression(self):
        self.assertEqual(w.ingest(self.db,self.event(),self.cfg,1000),'candidate')
        self.assertEqual(self.db.execute('select count(*) from deliveries').fetchone()[0],2)
        self.assertEqual(w.ingest(self.db,self.event(),self.cfg,1000),'duplicate_event')
        self.assertEqual(w.ingest(self.db,self.event(2,2),self.cfg,1001),'duplicate_content')
        self.cfg['mode']='live'
        w.set_candidate(self.db,1,'approved')
        with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':42}]) as api:
            self.assertEqual(w.dispatch(self.db,self.cfg,{},1002),1)
            self.assertEqual(api.call_args[0][2]['group_id'],3)
    def test_private_self_old_and_loop(self):
        for key,value in [('message_type','private'),('user_id',99),('time',0)]:
            event=self.event(); event[key]=value
            self.assertNotEqual(w.ingest(self.db,event,self.cfg,1000),'candidate')
        self.assertEqual(w.ingest(self.db,self.event(text=w.MARK+'A'),self.cfg,1000),'loop')
        self.assertEqual(self.db.execute('select count(*) from deliveries').fetchone()[0],0)
    def test_pending_and_preview_never_send(self):
        w.ingest(self.db,self.event(),self.cfg,1000)
        with patch('agent.onebot_call') as api:
            w.dispatch(self.db,self.cfg,{},1001)
            api.assert_not_called()
        w.set_candidate(self.db,1,'approved')
        with patch('agent.onebot_call') as api:
            w.dispatch(self.db,self.cfg,{},1001)
            api.assert_not_called()
    def test_rate_limit_and_no_ambiguous_retry(self):
        w.ingest(self.db,self.event(),self.cfg,1000)
        w.set_candidate(self.db,1,'approved'); self.cfg['mode']='live'
        with patch('agent.onebot_call',side_effect=[{'user_id':99},TimeoutError()]):
            self.assertEqual(w.dispatch(self.db,self.cfg,{},1001),0)
        self.assertEqual(self.db.execute("select count(*) from deliveries where status='uncertain'").fetchone()[0],1)
        with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':42}]):
            self.assertEqual(w.dispatch(self.db,self.cfg,{},1002),1)
        with patch('agent.onebot_call',return_value={'user_id':99}) as api:
            self.assertEqual(w.dispatch(self.db,self.cfg,{},1003),0)
            self.assertEqual(api.call_count,1)
    def test_context_bounds_and_retention(self):
        self.cfg.update(context_char_budget=100,context_messages=3,raw_retention_hours=1)
        for i in range(8): w.ingest(self.db,self.event(i,text='讨论'+str(i)*100),self.cfg,1000)
        ctx=w.context(self.db,1,self.cfg)
        self.assertLessEqual(sum(map(len,ctx['recent'])),100)
        self.assertEqual(ctx['summary']['recent_messages'],3)
        w.prune(self.db,self.cfg,5000)
        self.assertEqual(w.context(self.db,1,self.cfg)['recent'],[])
    def test_persistence_and_cdn_url_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.sqlite3'
            db=w.connect(path); w.ingest(db,self.event(images=True),self.cfg,1000); db.close()
            db=w.connect(path)
            event=self.event(2,2,images=True)
            event['message'][1]['data']['url']='https://example.com/a.png?token=2'
            self.assertEqual(w.ingest(db,event,self.cfg,1001),'duplicate_content')
            self.assertEqual(db.execute('select count(*) from candidates').fetchone()[0],1)
            db.close()
    def test_text_only_chat_is_context_and_contact_is_filtered(self):
        self.assertEqual(w.ingest(self.db,self.event(text='哈哈哈哈'),self.cfg,1000),'context_only')
        self.assertEqual(w.ingest(self.db,self.event(2,text='联系 13800138000 https://example.com'),self.cfg,1000),'context_only')
    def test_disabled_routes_and_account_mismatch(self):
        self.cfg['routes']={'1':['2','1','100']}
        w.ingest(self.db,self.event(),self.cfg,1000)
        self.assertEqual(self.db.execute('select count(*) from deliveries').fetchone()[0],1)
        w.set_candidate(self.db,1,'approved'); self.cfg['mode']='live'
        with patch('agent.onebot_call',return_value={'user_id':123}):
            with self.assertRaises(ValueError):w.dispatch(self.db,self.cfg,{},1001)
    def test_expired_candidate_not_sent(self):
        w.ingest(self.db,self.event(),self.cfg,1000);w.set_candidate(self.db,1,'approved')
        w.prune(self.db,self.cfg,1000+86401)
        self.assertEqual(self.db.execute("select count(*) from deliveries where status='expired'").fetchone()[0],2)

    def test_inactive_or_unknown_group_stays_silent(self):
        self.cfg.update(mode='live',routes={'1':['2','3']})
        self.db.execute('UPDATE group_activity SET last_member_message=? WHERE group_id=?',(-14*86400,'2'))
        self.db.execute('DELETE FROM group_activity WHERE group_id=?',('3',))
        self.db.commit()
        w.ingest(self.db,self.event(),self.cfg,1000);w.set_candidate(self.db,1,'approved')
        with patch('agent.onebot_call',return_value={'user_id':99}) as api:
            self.assertEqual(w.dispatch(self.db,self.cfg,{},1001),0)
            self.assertEqual(api.call_count,1)
        self.assertFalse(w.group_is_active(self.db,'2',self.cfg,1001))
        self.assertFalse(w.group_is_active(self.db,'3',self.cfg,1001))
        own=self.event(9,2);own['user_id']=99
        w.ingest(self.db,own,self.cfg,1001)
        self.assertFalse(w.group_is_active(self.db,'2',self.cfg,1001))

    def test_daily_limit_and_cooldown(self):
        self.cfg.update(mode='live',routes={'1':['2']},max_per_group_day=1,group_cooldown_seconds=0)
        w.ingest(self.db,self.event(),self.cfg,1000);w.set_candidate(self.db,1,'approved')
        with patch('agent.onebot_call',side_effect=[{'user_id':99},{'message_id':42}]):
            self.assertEqual(w.dispatch(self.db,self.cfg,{},1001),1)
        w.ingest(self.db,self.event(2,text='第二条 https://example.com/b'),self.cfg,1002)
        w.set_candidate(self.db,2,'approved')
        with patch('agent.onebot_call',return_value={'user_id':99}) as api:
            self.assertEqual(w.dispatch(self.db,self.cfg,{},1003),0)
            self.assertEqual(api.call_count,1)
        self.cfg.update(max_per_group_day=10,group_cooldown_seconds=3600)
        with patch('agent.onebot_call',return_value={'user_id':99}):
            self.assertEqual(w.dispatch(self.db,self.cfg,{},1100),0)

    def test_same_image_with_different_caption(self):
        w.ingest(self.db,self.event(images=True),self.cfg,1000)
        self.assertEqual(w.ingest(self.db,self.event(2,2,text='换了配文',images=True),self.cfg,1001),'duplicate_content')


if __name__=='__main__': unittest.main()
