import unittest,socket
from unittest.mock import patch
import internet,application

class InternetTests(unittest.TestCase):
 def test_private_and_mixed_dns_addresses_blocked(self):
  for ip in ('127.0.0.1','10.1.2.3','169.254.169.254','::1'):
   with patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,0,'',(ip,443))]):
    with self.assertRaises(ValueError):internet.endpoint('https://public.example/path')
  for url in ('file:///etc/passwd','http://user:pass@example.com','https://example.com:6099'):
   with self.assertRaises(ValueError):internet.endpoint(url)
 def test_search_structured_sources(self):
  rss='<rss><channel><item><title>Example</title><link>https://example.com/a</link><description>Text</description></item></channel></rss>'
  with patch('internet.fetch',return_value=('url',rss,'application/xml')):value=internet.search('test')
  self.assertEqual(value['results'][0]['url'],'https://example.com/a')
 def test_webpage_extracts_text_hides_script(self):
  with patch('internet.fetch',return_value=('https://example.com','<title>T</title><script>secret</script><p>hello</p>','text/html')):value=internet.read('https://example.com')
  self.assertIn('hello',value['text']);self.assertNotIn('secret',value['text'])
 def test_worker_tools_exposed_and_failure_is_a_result(self):
  call=application.decode('{"call":{"name":"web_search","arguments":{"query":"test"}}}')
  self.assertEqual(application.decision(call)['action'],'read')
  with patch('internet.search',side_effect=TimeoutError):result=application.read(None,{},'1',{},call)
  self.assertEqual(result['error'],'TimeoutError')
 def test_duplicate_tool_argument_rejected(self):
  with self.assertRaises(ValueError):application.decode('{"call":{"name":"read_webpage","arguments":{"url":"https://example.com","cookie":"private"}}}')

 def test_gzip_response_and_decompression_limit(self):
  import gzip
  self.assertEqual(internet.decode_body(gzip.compress(b'hello'),'gzip'),b'hello')
  with patch('internet.MAX_BYTES',100):
   with self.assertRaises(ValueError):internet.decode_body(gzip.compress(b'a'*1000),'gzip')
 def test_redirect_to_loopback_is_revalidated(self):
  from unittest.mock import Mock
  response=Mock(status=302);response.getheader.return_value='http://127.0.0.1/private'
  connection=Mock();connection.getresponse.return_value=response
  def dns(host,*args,**kwargs):return [(socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1' if host=='127.0.0.1' else '8.8.8.8',443))]
  with patch('socket.getaddrinfo',side_effect=dns),patch('internet.PublicHTTPS',return_value=connection):
   with self.assertRaisesRegex(ValueError,'private_address'):internet.fetch('https://example.com')

 def test_web_reply_keeps_source_after_long_answer(self):
  text='说明'*120+' https://example.com/source'
  call=application.decode(__import__('json').dumps({'call':{'name':'reply','arguments':{'text':text,'quality':'natural'}}}))
  self.assertTrue(application.decision(call)['text'].endswith('/source'))
  result=application.with_web_sources({'action':'reply','text':'说明'},{'tool_results':[{'call':{'name':'read_webpage'},'result':{'url':'https://example.com/source','text':'body'}}]})
  self.assertIn('https://example.com/source',result['text'])
