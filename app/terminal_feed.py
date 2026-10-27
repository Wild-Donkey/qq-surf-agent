"""One chronological stream for full and lite consoles; durable logs stay unchanged."""
import json,re
from pathlib import Path
from app import chat_view,service_log

STAMP=re.compile(r'^(\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
# 日志正文：label（可多词）+ key=value 字段。label 到第一个 key= 为止。
_LABEL_RE=re.compile(r'^(\S+(?:\s+\S+)*?)\s+(\w+=.*)$')
_FIELD_RE=re.compile(r'(\w+)=')
RETRACE=re.compile(r'group retrace complete group=(\S+) referents=\d+ notes=(.*)$')
ANALYSIS=re.compile(r'worker understanding group=(\S+) request_id=\S+ observations=(.*)$')
# QQ Helper 噪音：沙箱权限错误、Electron 调试、框架加载等无关输出
QQ_NOISE=re.compile(r'Permission denied|prelogin-log|\.qqxlog|frameworks path is not exists|IsVersionsDirExist|LoadFromDefaultFramework|\[preload\] (failed|succeeded)|Skia Graphite|not mini app|resourcesPath:|UnhandledPromiseRejection|at (Object|async|ModuleJob|process|file:)|Use .* to show where|Unhandled promise rejection|node:\d+\) ')

def _gname(groups,gid):
 return chat_view.shorten(groups.get(str(gid),str(gid))) if gid else ''

def _prefix(role,groups,gid):
 g=_gname(groups,gid)
 return '['+role+']'+('['+g+']' if g else '')

def _split_fields(rest):
 """把 'key1=val1 key2=val two' 解析为 dict；value 可含空格，到下一个 key= 为止。"""
 positions=[(m.start(),m.group(1)) for m in _FIELD_RE.finditer(rest)]
 fields={}
 for i,(pos,key) in enumerate(positions):
  vs=pos+len(key)+1
  ve=positions[i+1][0] if i+1<len(positions) else len(rest)
  fields[key]=rest[vs:ve].strip()
 return fields

def _parse_body(body):
 """返回 (label, fields)。无 key= 时 fields 为空。"""
 m=_LABEL_RE.match(body)
 if not m:return body.strip(),{}
 return m.group(1),_split_fields(m.group(2))

def _line(stamp,text):
 return (stamp+' ' if stamp else '')+text

# ---- label → (角色, 格式化函数)；格式化函数返回 list[str] ----
def _f_worker_started(f,groups):
 return [_prefix('worker',groups,'')+' 启动，模式='+f.get('mode','')]
def _f_worker_processing(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 处理 '+f.get('batch','?')+' 条消息']
def _f_worker_decided(f,groups):
 action=f.get('action','')
 reason=f.get('reason','')
 line=_prefix('worker',groups,f.get('group',''))+' 决定 '+action
 if reason:line+='：'+reason
 return [line]
def _f_worker_failed(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 决策失败：'+f.get('code','')]
def _f_worker_blocked(f,groups):
 code=f.get('code','').split(';')[0]
 return [_prefix('worker',groups,f.get('group',''))+' 决策被拦截：'+code]
def _f_worker_understanding_rejected(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 理解被拒：'+f.get('code','')]
def _f_worker_understanding_missing(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 理解缺失']
def _f_group_retrace_started(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 开始回溯 '+f.get('messages','?')+' 条消息']
def _f_group_retrace_requested(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 请求回溯：'+f.get('query','')]
def _f_memory_recalled(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 召回记忆 '+f.get('facts','?')+' 条']
def _f_planned_tool_call(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 调用工具：'+f.get('tool','')]
def _f_planned_tool_skipped(f,groups):
 tool=f.get('tool','')
 reason=f.get('reason','')
 line=_prefix('worker',groups,f.get('group',''))+' 跳过工具'+((' '+tool) if tool else '')+'：'+reason
 return [line]
def _f_planned_tool_failed(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 工具 '+f.get('tool','')+' 失败：'+f.get('err','')]
def _f_proactive_enqueue(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 主动入队']
def _f_proactive_initiate(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 主动发起']
def _f_proactive_clarification(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 开启主动澄清']
def _f_plan_missing(f,groups):
 return [_prefix('worker',groups,'')+' plan 缺字段：'+f.get('keys','')]
def _f_web_tool_blocked(f,groups):
 return [_prefix('worker',groups,'')+' 联网工具 '+f.get('name','')+' 被拦截：'+f.get('reason','')]
def _f_web_search(f,groups):
 return [_prefix('worker',groups,'')+' 搜索：'+f.get('query','')]
def _f_web_search_ok(f,groups):
 return [_prefix('worker',groups,'')+' 搜索完成：'+f.get('query','')+'（'+f.get('results','?')+' 条）']
def _f_web_read(f,groups):
 return [_prefix('worker',groups,'')+' 读取：'+f.get('url','')]
def _f_web_read_ok(f,groups):
 return [_prefix('worker',groups,'')+' 读取完成：'+f.get('url','')+'（'+f.get('title','')+'）']
def _f_web_tool_failed(f,groups):
 return [_prefix('worker',groups,'')+' 联网工具 '+f.get('name','')+' 失败：'+f.get('error','')]
def _f_review_ok(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 审核通过：delivery='+f.get('delivery','')]
def _f_review_needs_revision(f,groups):
 issues=f.get('issues','')
 out=[_prefix('worker',groups,f.get('group',''))+' 需改进：']
 try:
  arr=json.loads(issues)
  if isinstance(arr,list):
   for it in arr:out.append('  - '+str(it))
  else:out.append('  '+issues[:120])
 except (ValueError,TypeError):
  out.append('  '+issues[:120])
 return out
def _f_review_failed(f,groups):
 return [_prefix('worker',groups,'')+' 审核失败：delivery='+f.get('delivery','')+'，'+f.get('err','')]
def _f_recall_before_resend_failed(f,groups):
 return [_prefix('worker',groups,'')+' 撤回失败：delivery='+f.get('delivery','')+'，'+f.get('err','')]
def _f_revised_resend(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 重发完成：delivery='+f.get('delivery','')+' recalled='+f.get('recalled','')]
def _f_revised_resend_failed(f,groups):
 return [_prefix('worker',groups,'')+' 重发失败：delivery='+f.get('delivery','')+'，'+f.get('err','')]
def _f_private_turn(f,groups):
 return [_prefix('worker',groups,'')+' 私聊决策 user='+f.get('user','')+' action='+f.get('action','')]

def _f_event(f,groups,post_type):
 result=f.get('result','')
 text=f.get('text','')
 cn={'message':'消息','notice':'通知','request':'请求'}.get(post_type,post_type)
 line=_prefix('supervisor',groups,f.get('group',''))+' '+cn+'事件：'+result
 # message 的文本已在 QQ 聊天行展示，此处不重复
 if text and post_type!='message':line+=' '+text
 return [line]
def _f_received_poke(f,groups):
 return [_prefix('supervisor',groups,f.get('group',''))+' 收到戳一戳：user='+f.get('user','')]
def _f_owner_recall(f,groups):
 return [_prefix('supervisor',groups,f.get('group',''))+' 撤回 message_id='+f.get('message_id','')]
def _f_owner_recall_failed(f,groups):
 return [_prefix('supervisor',groups,f.get('group',''))+' 撤回失败：'+f.get('error','')]
def _f_delivery_sent(f,groups):
 return [_prefix('supervisor',groups,f.get('group',''))+' 投递完成：'+f.get('kind','')+'（id='+f.get('id','')+' message_id='+f.get('message_id','')+'）']
def _f_operator_prompt_queued(f,groups):
 return [_prefix('supervisor',groups,f.get('group',''))+' 操作员提示入队：id='+f.get('id','')]
def _f_operator_prompt_applied(f,groups):
 return [_prefix('supervisor',groups,'')+' 操作员提示已应用：id='+f.get('id','')+' action='+f.get('action','')]
def _f_operator_pause(f,groups):
 m=f.get('minutes','0')
 return [_prefix('supervisor',groups,f.get('group',''))+(' 恢复自动回复' if m=='0' else ' 暂停 '+m+' 分钟')]
def _f_owner_manual_message(f,groups):
 return [_prefix('supervisor',groups,f.get('group',''))+' 群主手动消息：message_id='+f.get('message_id','')]
def _f_owner_notification_pending(f,groups):
 return [_prefix('supervisor',groups,f.get('group',''))+' 群主通知待发']
def _f_owner_image_notification_pending(f,groups):
 return [_prefix('supervisor',groups,f.get('group',''))+' 群主图片通知待发']

# worker traceback 多行：首行标 worker + 群，后续 traceback 行原样跟随
def _f_worker_traceback(f,groups):
 return [_prefix('worker',groups,f.get('group',''))+' 异常']

FORMATTERS={
 'worker started':_f_worker_started,
 'worker processing':_f_worker_processing,
 'worker decided':_f_worker_decided,
 'worker failed':_f_worker_failed,
 'worker blocked':_f_worker_blocked,
 'worker understanding rejected':_f_worker_understanding_rejected,
 'worker understanding missing':_f_worker_understanding_missing,
 'group retrace started':_f_group_retrace_started,
 'group retrace requested':_f_group_retrace_requested,
 'memory recalled':_f_memory_recalled,
 'planned tool call':_f_planned_tool_call,
 'planned tool skipped':_f_planned_tool_skipped,
 'planned tool failed':_f_planned_tool_failed,
 'proactive enqueue':_f_proactive_enqueue,
 'proactive initiate':_f_proactive_initiate,
 'proactive clarification enabled':_f_proactive_clarification,
 'plan missing':_f_plan_missing,
 'web tool blocked':_f_web_tool_blocked,
 'web search':_f_web_search,
 'web search ok':_f_web_search_ok,
 'web read':_f_web_read,
 'web read ok':_f_web_read_ok,
 'web tool failed':_f_web_tool_failed,
 'review ok':_f_review_ok,
 'review needs revision':_f_review_needs_revision,
 'review failed':_f_review_failed,
 'recall before resend failed':_f_recall_before_resend_failed,
 'revised resend':_f_revised_resend,
 'revised resend failed':_f_revised_resend_failed,
 'private turn':_f_private_turn,
 'received poke':_f_received_poke,
 'owner recall':_f_owner_recall,
 'owner recall failed':_f_owner_recall_failed,
 'delivery sent':_f_delivery_sent,
 'operator prompt queued':_f_operator_prompt_queued,
 'operator prompt applied':_f_operator_prompt_applied,
 'operator pause':_f_operator_pause,
 'owner manual message':_f_owner_manual_message,
 'owner notification pending':_f_owner_notification_pending,
 'owner image notification pending':_f_owner_image_notification_pending,
}

def _format_app(body,groups):
 """把 surf-bot.log 一行正文（去时间戳后）转成终端行；返回 list[str]。"""
 # 先处理带 JSON 展开的特殊行（retrace / understanding）
 retrace=RETRACE.search(body)
 if retrace:
  try:notes=json.loads(retrace[2])
  except (ValueError,TypeError):notes=None
  if isinstance(notes,dict):
   prefix=_prefix('worker',groups,retrace[1])+' 回溯完成'
   out=[prefix]
   has=False
   for note in notes.get('topics',[]):
    out.append('  话题：'+str(note.get('text','') if isinstance(note,dict) else note));has=True
   for ref in notes.get('referents',[]):
    if isinstance(ref,dict):
     out.append('  指代：'+str(ref.get('expression',''))+' → '+str(ref.get('interpretation',''))+'（'+{'confirmed':'确认','tentative':'推测','unknown':'未知'}.get(ref.get('certainty'),'未知')+'）');has=True
   for note in notes.get('open_interactions',[]):
    out.append('  待续：'+str(note.get('text','') if isinstance(note,dict) else note));has=True
   if not has:out.append('  未建立确定话题或指代')
   return out
 match=ANALYSIS.search(body)
 if match:
  try:notes=json.loads(match[2])
  except (ValueError,TypeError):notes=None
  if isinstance(notes,dict):
   prefix=_prefix('worker',groups,match[1])+' 局面理解'
   out=[prefix]
   if notes.get('summary'):out.append('  '+str(notes['summary']))
   for image in notes.get('images',[]):
    if isinstance(image,dict) and image.get('description'):out.append('  图片 '+str(image.get('reference',''))+'：'+str(image['description']))
   for unknown in notes.get('uncertainties',[]):out.append('  待确认：'+str(unknown))
   if not any((notes.get('summary'),notes.get('images'),notes.get('uncertainties'))):out.append('  本轮未记录具体理解')
   return out
 label,fields=_parse_body(body)
 # event message/notice/request
 if label.startswith('event '):
  return _f_event(fields,groups,label[6:])
 fn=FORMATTERS.get(label)
 if fn:return fn(fields,groups)
 # 未识别 label：保持 label 文字，字段用自然语言拼接
 g=fields.get('group','')
 prefix=_prefix('worker' if label.startswith(('worker','group ','memory ','planned ','proactive','plan ','web ','review','recall','revised','private')) else 'supervisor',groups,g)
 rest=body[len(label):].strip() if body.startswith(label) else body
 return [prefix+' '+label+((' '+rest) if rest else '')]

def format_lines(lines,source,groups=None):
 result=[];groups=groups or {}
 continuing=False  # worker traceback 多行续行
 for raw in lines:
  raw=service_log.clean(raw)
  if source=='QQ' and QQ_NOISE.search(raw):continue
  stamp=STAMP.match(raw);stamp_txt=stamp[1] if stamp else ''
  body=raw[len(stamp[0]):].lstrip() if stamp else raw
  if source=='QQ':
   row=chat_view.parse(raw)
   if row.group:result.append(raw);continue
   result.append(_line(stamp_txt,'[QQ] '+(row.text[9:] if stamp else raw)));continue
  # surf-bot.log 正文
  if source=='应用':
   # worker traceback 续行：缩进行或 File 行直接跟随，不重打前缀
   if continuing and (body.startswith(' ') or body.startswith('File ') or body.startswith('Traceback') or body.startswith('  ')):
    result.append(_line(stamp_txt,'  '+body));continue
   continuing=False
   if body.startswith('worker traceback '):
    label,fields=_parse_body(body)
    result.append(_line(stamp_txt,_f_worker_traceback(fields,groups)[0]))
    continuing=True;continue
   for formatted in _format_app(body,groups):
    result.append(_line(stamp_txt,formatted))
   continue
  # workflow.log（服务）：supervisor 捕获的 print 输出
  row=chat_view.parse(raw)
  result.append(_line(stamp_txt,'[supervisor] '+(row.text[9:] if stamp else raw)))
 return result

class Feed:
 def __init__(self,root):
  from app.console import Tail
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
