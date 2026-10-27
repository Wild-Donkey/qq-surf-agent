"""chatgpt, internet regression coverage."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from app import chatgpt_auth as auth
from app import social
import socket
from app import internet
from app import application
import urllib.error
from app import workflow


class ChatGPTTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.private=patch.object(auth,'PRIVATE',Path(self.tmp.name));self.private.start()
        self.pending={'state':'s','nonce':'n','verifier':'v','host_id':'h','redirect_uri':'http://127.0.0.1:8768/auth/callback','expires_at':time.time()+60}
        self.tokens={'access_token':'access','refresh_token':'refresh','id_token':'id','expires_in':3600,'scope':auth.SCOPE}
    def tearDown(self):self.private.stop();self.tmp.cleanup()
    def finish(self):
        with patch.object(auth,'request_json',return_value=self.tokens),patch.object(auth,'verify_identity',return_value={'sub':'user'}):
            auth.complete({'state':'s','code':'c','client_id':'issued'},self.pending)
    def test_invalid_callback_never_exchanges_code(self):
        for params in [{'state':'wrong','code':'c','client_id':'issued'}, {'state':'s','error':'access_denied'}, {'state':'s','code':'c'}]:
            with patch.object(auth,'request_json') as request:
                with self.assertRaises(ValueError):auth.complete(params,self.pending)
                request.assert_not_called()
    def test_no_plan_permission_preserves_previous_connection(self):
        self.finish();old=auth.active_record()[1]
        tokens=dict(self.tokens,scope='openid')
        with patch.object(auth,'request_json',return_value=tokens),patch.object(auth,'verify_identity',return_value={'sub':'other'}):
            with self.assertRaises(ValueError):auth.complete({'state':'s','code':'c','client_id':'another'},self.pending)
        self.assertEqual(auth.active_record()[1],old)
    def test_identity_validation_failure_stores_nothing(self):
        with patch.object(auth,'request_json',return_value=self.tokens),patch.object(auth,'verify_identity',side_effect=ValueError('bad signature')):
            with self.assertRaises(ValueError):auth.complete({'state':'s','code':'c','client_id':'issued'},self.pending)
        self.assertFalse(auth.available())
    def test_signed_identity_rejects_wrong_nonce_audience_expiry_and_signature(self):
        sys.path.insert(0,str(auth.ROOT/'runtime'/'deps'))
        try:
            import jwt
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:self.skipTest('optional ChatGPT login dependencies not installed')
        from types import SimpleNamespace
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        wrong=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        claims={'sub':'user','iss':auth.AUTH,'aud':'issued','nonce':'n','iat':int(time.time()),'exp':int(time.time()+60)}
        discovery={'issuer':auth.AUTH,'jwks_uri':auth.AUTH+'/.well-known/jwks.json'}
        with patch.object(auth,'request_json',return_value=discovery),patch.object(jwt,'PyJWKClient') as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value=SimpleNamespace(key=key.public_key())
            self.assertEqual(auth.verify_identity({'id_token':jwt.encode(claims,key,algorithm='RS256')},'issued','n')['sub'],'user')
            for changed,signer in [(dict(claims,nonce='wrong'),key),(dict(claims,aud='another'),key),(dict(claims,exp=int(time.time()-60)),key),(dict(claims,iss='https://evil.example'),key),(claims,wrong)]:
                with self.assertRaises((ValueError,jwt.PyJWTError)):
                    auth.verify_identity({'id_token':jwt.encode(changed,signer,algorithm='RS256')},'issued','n')
    def test_atomic_private_storage_and_rotating_refresh(self):
        self.finish();path,record=auth.active_record()
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        record['expires_at']=0;auth.save(path,record)
        tokens=dict(self.tokens,access_token='new-access',refresh_token='new-refresh')
        with patch.object(auth,'request_json',return_value=tokens) as request:
            self.assertEqual(auth.access_token(),'new-access')
            self.assertEqual(request.call_args[0][1]['client_id'],'issued')
        self.assertEqual(auth.active_record()[1]['refresh_token'],'new-refresh')
    def test_stream_must_complete_and_uses_public_responses(self):
        import io
        data={'type':'response.completed','response':{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"action":"wait"}'}]}],'usage':{'input_tokens':12}}}
        stream=io.BytesIO(('data: '+json.dumps(data)+'\n\n').encode())
        with patch.object(auth,'access_token',return_value='secret'),patch.object(auth.urllib.request,'urlopen',return_value=stream) as request:
            text,usage=auth.infer('selected','rules',{'context':'sample'})
            req=request.call_args[0][0];body=json.loads(req.data)
            self.assertEqual(req.full_url,auth.RESOURCE+'/responses')
            self.assertFalse(body['store']);self.assertTrue(body['stream'])
            self.assertNotIn('max_output_tokens',body)
            self.assertEqual(usage['prompt_tokens'],12)
        with patch.object(auth,'access_token',return_value='secret'),patch.object(auth.urllib.request,'urlopen',return_value=io.BytesIO(b'')):
            with self.assertRaises(ValueError):auth.infer('selected','rules',{})
    def test_planner_uses_oauth_adapter(self):
        with patch.object(auth,'available',return_value=True),patch.object(auth,'infer',return_value=('{"call":{"name":"wait","arguments":{}}}',{})):
            result,_=social.call_model({'enabled':True,'provider':'chatgpt','model':'selected'}, {})
            self.assertEqual(result['action'],'wait')
    def test_stream_final_items_survive_empty_completed_output(self):
        import io
        events=[{'type':'response.output_item.done','output_index':0,'item':{'type':'message','phase':'commentary','content':[{'type':'output_text','text':'Thinking'}]}},
                {'type':'response.output_item.done','output_index':1,'item':{'type':'message','phase':'final_answer','content':[{'type':'output_text','text':'{"action":"wait"}'}]}},
                {'type':'response.completed','response':{'status':'completed','output':[]}}]
        stream=lambda items:io.BytesIO(''.join('data: '+json.dumps(e)+'\n\n' for e in items).encode())
        with patch.object(auth,'access_token',return_value='secret'),patch.object(auth.urllib.request,'urlopen',return_value=stream(events)):
            self.assertEqual(auth.infer('selected','rules',{})[0],'{"action":"wait"}')
        with patch.object(auth,'access_token',return_value='secret'),patch.object(auth.urllib.request,'urlopen',return_value=stream(events[:-1])):
            with self.assertRaises(ValueError):auth.infer('selected','rules',{})


class InternetTests(unittest.TestCase):
 def test_private_and_mixed_dns_addresses_blocked(self):
  for ip in ('127.0.0.1','10.1.2.3','169.254.169.254','::1'):
   with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,0,'',(ip,443))]):
    with self.assertRaises(ValueError):internet.endpoint('https://public.example/path')
  for url in ('file:///etc/passwd','http://user:pass@example.com','https://example.com:6099'):
   with self.assertRaises(ValueError):internet.endpoint(url)
 def test_search_structured_sources(self):
  rss='<rss><channel><item><title>Example</title><link>https://example.com/a</link><description>Text</description></item></channel></rss>'
  with patch('app.internet.fetch',return_value=('url',rss,'application/xml')):value=internet.search('test')
  self.assertEqual(value['results'][0]['url'],'https://example.com/a')
 def test_webpage_extracts_text_hides_script(self):
  with patch('app.internet.fetch',return_value=('https://example.com','<title>T</title><script>secret</script><p>hello</p>','text/html')):value=internet.read('https://example.com')
  self.assertIn('hello',value['text']);self.assertNotIn('secret',value['text'])
 def test_worker_tools_exposed_and_failure_is_a_result(self):
  call=application.decode('{"call":{"name":"web_search","arguments":{"query":"test"}}}')
  self.assertEqual(application.decision(call)['action'],'read')
  with patch('app.internet.search',side_effect=TimeoutError):result=application.read(None,{},'1',{},call)
  self.assertEqual(result['error'],'TimeoutError')
 def test_duplicate_tool_argument_rejected(self):
  with self.assertRaises(ValueError):application.decode('{"call":{"name":"read_webpage","arguments":{"url":"https://example.com","cookie":"private"}}}')

 def test_gzip_response_and_decompression_limit(self):
  import gzip
  self.assertEqual(internet.decode_body(gzip.compress(b'hello'),'gzip'),b'hello')
  with patch('app.internet.MAX_BYTES',100):
   with self.assertRaises(ValueError):internet.decode_body(gzip.compress(b'a'*1000),'gzip')
 def test_redirect_to_loopback_is_revalidated(self):
  from unittest.mock import Mock
  response=Mock(status=302);response.getheader.return_value='http://127.0.0.1/private'
  connection=Mock();connection.getresponse.return_value=response
  def dns(host,*args,**kwargs):return [(socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1' if host=='127.0.0.1' else '8.8.8.8',443))]
  with patch('socket.getaddrinfo',side_effect=dns),patch('app.internet.PublicHTTPS',return_value=connection):
   with self.assertRaisesRegex(ValueError,'private_address'):internet.fetch('https://example.com')

 def test_web_reply_keeps_source_after_long_answer(self):
  text='说明'*120+' https://example.com/source'
  call=application.decode(__import__('json').dumps({'call':{'name':'reply','arguments':{'text':text,'quality':'natural'}}}))
  self.assertTrue(application.decision(call)['text'].endswith('/source'))
  result=application.with_web_sources({'action':'reply','text':'说明'},{'tool_results':[{'call':{'name':'read_webpage'},'result':{'url':'https://example.com/source','text':'body'}}]})
  self.assertIn('https://example.com/source',result['text'])


class ModelRetryTests(unittest.TestCase):

    def test_transport_errors_are_not_retried(self):
        exc = urllib.error.HTTPError('https://example.com', 429, 'quota', {}, None)
        with patch('app.social.generate_text', side_effect=exc) as gen:
            with self.assertRaises(urllib.error.HTTPError):
                social.call_model({}, {})
            self.assertEqual(gen.call_count, 1)

    def test_retry_reserved_accounted_and_kept_fast(self):
        calls = []
        done = []

        def generate(model, *args):
            calls.append(model)
            return ('bad' if len(calls) == 1 else '{"call":{"name":"wait","arguments":{}}}', {'prompt_tokens': 10, 'completion_tokens': 2})
        model = {'reasoning_effort': 'none', '_retry_reserve': lambda usage: 42, '_retry_complete': lambda *args: done.append(args)}
        with patch('app.social.generate_text', side_effect=generate):
            (d, u) = social.call_model(model, {})
        self.assertEqual([c['reasoning_effort'] for c in calls], ['none', 'none'])
        self.assertEqual(done[0][:2], (42, True))
        self.assertEqual(done[0][2]['prompt_tokens'], 10)
        self.assertEqual(u['prompt_tokens'], 10)

    def test_retry_timeout_keeps_specific_error(self):
        done = []
        model = {'_retry_reserve': lambda usage: 42, '_retry_complete': lambda *args: done.append(args)}
        with patch('app.social.generate_text', side_effect=[('bad', {}), TimeoutError('read timed out')]):
            with self.assertRaises(TimeoutError):
                social.call_model(model, {})
        self.assertEqual(done[0][2]['_error'], 'TimeoutError')

    def test_standalone_invalid_output_does_not_make_unreserved_retry(self):
        with patch('app.social.generate_text', return_value=('bad', {})) as gen:
            with self.assertRaises(ValueError):
                social.call_model({}, {})
            self.assertEqual(gen.call_count, 1)


if __name__ == '__main__':
    unittest.main()
