import tempfile,time,json,unittest
from unittest.mock import patch
from app import workflow,clarification,identities,request_queue,social

class ClarificationTests(unittest.TestCase):
 def test_ask_links_request_waits_and_wakes_on_answer(self):
  with tempfile.TemporaryDirectory() as root:
   db=workflow.connect(':memory:');now=time.time()
   cfg={'context_dir':root,'bot_id':'99','groups':[{'id':1,'name':'A'}],'mode':'live','reply_delay_seconds':0,'max_per_group_day':None,'max_total_day':None,'interaction':{'enabled':True}}
   event={'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':123,'message':[{'type':'at','data':{'qq':'99'}},{'type':'text','data':{'text':'教我那个测试术语'}}]}
   workflow.ingest(db,event,cfg,now);r=identities.Registry(root);r.alias('7','阿甲');r.close()
   payload={'pending_requests':request_queue.pending(db,'1')}
   d=clarification.prepare(db,cfg,'1',{'action':'ask','alias':'阿甲','text':'你说的测试术语指哪个？','request_ids':['1:123']},payload)
   accepted=request_queue.accepts(db,'1',d,payload['pending_requests']);self.assertEqual(accepted,['1:123'])
   delivery=social.apply_decision(db,{'target_group':'1','source_group':'1','event_key':'1:123','trigger_text':''},d,{},cfg,now)
   clarification.bind(db,'1',d,delivery);request_queue.bind(db,'1',accepted,d,delivery,now)
   with patch('app.agent.onebot_call',side_effect=[{'user_id':99},{'message_id':456}]):workflow.dispatch(db,cfg,{},now+1)
   request_queue.sync(db,cfg,now+2)
   self.assertEqual(db.execute("SELECT status FROM user_requests WHERE event_key='1:123'").fetchone()[0],'waiting_info')
   answer={**event,'message_id':124,'message':[{'type':'text','data':{'text':'我指的是红黑树'}}]}
   workflow.ingest(db,answer,cfg,now+3)
   pending=request_queue.pending(db,'1');self.assertEqual(pending[0]['possible_clarification_answer']['event_key'],'1:124')
   db.close()
