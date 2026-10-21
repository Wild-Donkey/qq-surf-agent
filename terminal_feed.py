"""One chronological stream for full and lite consoles; durable logs stay unchanged."""
import json,re
from pathlib import Path
import chat_view,service_log

STAMP=re.compile(r'^(\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
RETRACE=re.compile(r'group retrace complete group=(\S+) referents=\d+ notes=(.*)$')
ANALYSIS=re.compile(r'worker understanding group=(\S+) request_id=\S+ observations=(.*)$')

def format_lines(lines,source,groups=None):
 result=[];groups=groups or {}
 for raw in lines:
  raw=service_log.clean(raw)
  retrace=RETRACE.search(raw)
  if retrace:
   try:notes=json.loads(retrace[2])
   except (ValueError,TypeError):notes=None
   if isinstance(notes,dict):
    stamp=STAMP.match(raw);prefix=(stamp[1]+' ' if stamp else '')+'[回溯]['+chat_view.shorten(groups.get(retrace[1],retrace[1]))+'] '
    for note in notes.get('topics',[]):result.append(prefix+'话题：'+str(note.get('text','') if isinstance(note,dict) else note))
    for ref in notes.get('referents',[]):
     if isinstance(ref,dict):result.append(prefix+'指代：'+str(ref.get('expression',''))+' → '+str(ref.get('interpretation',''))+'（'+{'confirmed':'确认','tentative':'推测','unknown':'未知'}.get(ref.get('certainty'),'未知')+'）')
    for note in notes.get('open_interactions',[]):result.append(prefix+'待续：'+str(note.get('text','') if isinstance(note,dict) else note))
    if not any(notes.values()):result.append(prefix+'预读完成，未建立确定话题或指代')
    continue
  match=ANALYSIS.search(raw)
  if match:
   try:notes=json.loads(match[2])
   except (ValueError,TypeError):notes=None
   if isinstance(notes,dict):
    stamp=STAMP.match(raw);prefix=(stamp[1]+' ' if stamp else '')+'[理解]['+chat_view.shorten(groups.get(match[1],match[1]))+'] '
    if notes.get('summary'):result.append(prefix+str(notes['summary']))
    for image in notes.get('images',[]):
     if isinstance(image,dict) and image.get('description'):result.append(prefix+'图片 '+str(image.get('reference',''))+'：'+str(image['description']))
    for unknown in notes.get('uncertainties',[]):result.append(prefix+'待确认：'+str(unknown))
    if not notes.get('summary') and not notes.get('images') and not notes.get('uncertainties'):result.append(prefix+'本轮未记录具体理解')
    continue
  row=chat_view.parse(raw)
  if source=='QQ' and row.group:result.append(raw);continue
  # Preserve one timestamp and clearly label service output in the same stream.
  stamp=STAMP.match(raw)
  result.append((stamp[1]+' ' if stamp else '')+'['+source+'] '+(row.text[9:] if stamp else raw))
 return result

class Feed:
 def __init__(self,root):
  from console import Tail
  self.root=Path(root)
  self.tails=[('QQ',Tail(self.root/'runtime/logs/qq-helper.log')),('应用',Tail(self.root/'runtime/logs/surf-bot.log')),('服务',Tail(self.root/'runtime/workflow.log'))]
  if self.tails[1][1].path.exists():self.tails[2][1].read()
 def read(self):
  try:groups={str(g['id']):g['name'] for g in json.loads((self.root/'workflow.json').read_text()).get('groups',[])}
  except (OSError,ValueError,KeyError):groups={}
  batches=[]
  for source,tail in self.tails:
   try:fresh=tail.read()
   except OSError:continue
   for line in format_lines(fresh,source,groups):
    stamp=STAMP.match(line);batches.append((stamp[1] if stamp else '',len(batches),line))
  return [line for _,_,line in sorted(batches)]
