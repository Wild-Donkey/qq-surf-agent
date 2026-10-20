import hashlib,hmac,http.client,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workflow

class ChunkTests(unittest.TestCase):
    def test_signed_napcat_chunked_event_and_invalid_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath=Path(tmp)/'events.db';db=workflow.connect(dbpath);db.close()
            cfg={'bot_id':'99','groups':[{'id':1,'name':'Test'}]}
            server=ThreadingHTTPServer(('127.0.0.1',0),workflow.handler(dbpath,cfg,'secret'))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                event={'post_type':'message','message_type':'group','group_id':1,'user_id':7,'message_id':1,'time':time.time(),'message':[{'type':'text','data':{'text':'hello'}}]}
                body=json.dumps(event).encode();sig='sha1='+hmac.new(b'secret',body,hashlib.sha1).hexdigest()
                for signature,status in [(sig,200),('sha1=invalid',403)]:
                    client=http.client.HTTPConnection(*server.server_address)
                    client.request('POST','/events',body=iter([body[:15],body[15:]]),headers={'X-Signature':signature},encode_chunked=True)
                    response=client.getresponse();self.assertEqual(response.status,status);response.read();client.close()
                db=workflow.connect(dbpath)
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1);db.close()
            finally:server.shutdown();server.server_close();thread.join()
