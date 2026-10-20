"""Stage-specific, content-free protocol failures and strict JSON envelopes."""
import hashlib,json,re
class ProtocolError(ValueError):
 def __init__(self,stage,code,details=None):
  super().__init__(code);self.stage=stage;self.code=code;self.details={'stage':stage,**(details or {})}
def parse_json(text,stage):
 if not isinstance(text,str):raise ProtocolError(stage,'invalid_json_type')
 text=text.lstrip('\ufeff').strip()
 if stage!='sse_event':
  match=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```',text,flags=re.S|re.I)
  if match:text=match[1].strip()
 try:return json.loads(text)
 except json.JSONDecodeError as exc:
  raise ProtocolError(stage,stage+'_invalid_json',{'length':len(text),'position':exc.pos,'sha256':hashlib.sha256(text.encode()).hexdigest()}) from None
