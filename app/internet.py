"""Read-only public web tools. Bounded responses, pinned public DNS, no account cookies."""
import datetime,html,http.client,ipaddress,json,socket,ssl,urllib.parse,xml.etree.ElementTree as ET,zlib
from html.parser import HTMLParser

MAX_BYTES=2*1024*1024

def endpoint(url):
 if not isinstance(url,str) or len(url)>2048:raise ValueError('invalid_web_url')
 p=urllib.parse.urlsplit(url)
 if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80 if p.scheme=='http' else 443):raise ValueError('invalid_web_url')
 addresses=socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)
 if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):raise ValueError('web_private_address_blocked')
 return p,addresses[0][4][0]

class PublicHTTPS(http.client.HTTPSConnection):
 def __init__(self,host,ip,port):super().__init__(host,port,timeout=10,context=ssl.create_default_context());self.ip=ip
 def connect(self):
  raw=socket.create_connection((self.ip,self.port),self.timeout)
  try:self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
  except Exception:raw.close();raise

class PublicHTTP(http.client.HTTPConnection):
 def __init__(self,host,ip,port):super().__init__(host,port,timeout=10);self.ip=ip
 def connect(self):self.sock=socket.create_connection((self.ip,self.port),self.timeout)

def decode_body(raw,encoding):
 if len(raw)>MAX_BYTES:raise ValueError('web_page_too_large')
 if encoding in ('gzip','deflate'):
  decoder=zlib.decompressobj(31 if encoding=='gzip' else zlib.MAX_WBITS)
  raw=decoder.decompress(raw,MAX_BYTES+1)
  if len(raw)>MAX_BYTES or decoder.unconsumed_tail:raise ValueError('web_page_too_large')
  if not decoder.eof:raise ValueError('web_truncated_compression')
 elif encoding not in ('','identity'):raise ValueError('web_unsupported_encoding')
 return raw

def fetch(url):
 for _ in range(5):
  p,ip=endpoint(url);conn=(PublicHTTPS if p.scheme=='https' else PublicHTTP)(p.hostname,ip,p.port)
  try:
   path=urllib.parse.urlunsplit(('', '', p.path or '/',p.query,''))
   conn.request('GET',path,headers={'User-Agent':'Mozilla/5.0 (compatible; LunaSurf/1.0)','Accept-Encoding':'identity','Accept':'text/html,application/xhtml+xml,application/rss+xml,text/plain,application/xml'})
   response=conn.getresponse()
   if response.status in (301,302,303,307,308):
    location=response.getheader('Location')
    if not location:raise ValueError('web_redirect_without_location')
    url=urllib.parse.urljoin(url,location);continue
   if response.status!=200:raise ValueError('web_http_'+str(response.status))
   content_type=response.getheader('Content-Type','').lower()
   if not any(kind in content_type for kind in ('text/','xml','json')):raise ValueError('web_unsupported_content_type')
   raw=response.read(MAX_BYTES+1)
   raw=decode_body(raw,response.getheader('Content-Encoding','').lower())
   import re
   match=re.search(r'charset=["\']?([\w-]+)',content_type)
   encoding=match[1] if match else 'utf-8'
   try:body=raw.decode(encoding,errors='replace')
   except LookupError:body=raw.decode('utf-8',errors='replace')
   return url,body,content_type
  finally:conn.close()
 raise ValueError('web_too_many_redirects')

class Text(HTMLParser):
 def __init__(self):super().__init__();self.hidden=0;self.parts=[];self.title=[];self.in_title=False
 def handle_starttag(self,tag,attrs):
  if tag in ('script','style','noscript','svg'):self.hidden+=1
  if tag=='title':self.in_title=True
  if tag in ('p','div','br','li','h1','h2','h3','tr','article'):self.parts.append('\n')
 def handle_endtag(self,tag):
  if tag in ('script','style','noscript','svg'):self.hidden=max(0,self.hidden-1)
  if tag=='title':self.in_title=False
 def handle_data(self,data):
  if not self.hidden:
   self.parts.append(data)
   if self.in_title:self.title.append(data)

def read(url):
 final,body,kind=fetch(url)
 if 'html' in kind:
  parser=Text();parser.feed(body);title=''.join(parser.title);text=''.join(parser.parts)
 else:title='';text=body
 lines=[' '.join(line.split()) for line in text.splitlines()];text='\n'.join(line for line in lines if line)
 return {'url':final,'title':title[:300],'text':text[:10000],'truncated':len(text)>10000,'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'untrusted_web_content':True}

def search(query):
 if not isinstance(query,str) or not query.strip() or len(query)>300:raise ValueError('invalid_web_query')
 url='https://www.bing.com/search?'+urllib.parse.urlencode({'q':query,'format':'rss'})
 _,body,_=fetch(url);root=ET.fromstring(body);results=[]
 for item in root.findall('.//item')[:5]:
  link=item.findtext('link','')
  if urllib.parse.urlsplit(link).scheme not in ('http','https'):continue
  parser=Text();parser.feed(item.findtext('description',''))
  results.append({'title':html.unescape(item.findtext('title',''))[:200],'url':link,'snippet':' '.join(''.join(parser.parts).split())[:700]})
 return {'query':query,'results':results,'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'untrusted_web_content':True}

def tool(cfg,name,args):
 if not cfg.get('internet',{}).get('enabled',True):return {'error':'internet_disabled'}
 try:return search(args.get('query')) if name=='web_search' else read(args.get('url'))
 except Exception as exc:
  code=str(exc) if isinstance(exc,ValueError) and str(exc).startswith(('web_','invalid_web_')) else type(exc).__name__
  return {'error':code[:100],'instruction':'联网未成功，不得声称已查到或编造结果；可换搜索词或说明暂时无法读取。'}
