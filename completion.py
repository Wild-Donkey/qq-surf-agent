"""Discoverable command, service-action and group completion; no command execution."""
COMMANDS={
 '/groups':'列出可用群','/use':'选择群（支持群名或群号）','/say':'发送带人工接管标记的消息',
 '/takeover':'接管当前群，默认5分钟','/pause':'暂停自动回复','/resume':'恢复自动回复',
 '/worker':'给当前群worker提交prompt','/jobs':'查看worker指令状态','/status':'查看服务和接管状态',
 '/bot':'管理surf bot','/helper':'管理QQ Helper','/help':'查看所有命令','/quit':'退出界面，保留后台服务'}
ACTIONS={'/bot':{'start':'启动bot','restart':'重启worker','stop':'停止bot服务'},
 '/helper':{'status':'查看Helper状态','start':'启动QQ Helper','wrap':'封装现有QQ并持久记录日志','stop':'停止受管理的QQ Helper'},
 '/takeover':{str(n):'接管'+str(n)+'分钟' for n in (5,10,30,60)},'/pause':{str(n):'暂停'+str(n)+'分钟' for n in (5,10,30,60)}}

def candidates(prefix,groups):
 if prefix and not prefix.startswith('/'):return []
 if ' ' not in prefix:return [(key+' ',COMMANDS[key]) for key in COMMANDS if key.startswith(prefix)]
 name,arg=prefix.split(' ',1)
 if name=='/use':
  needle=arg.strip().casefold();result=[]
  for g in groups:
   if not g.get('enabled',True):continue
   ident=str(g['id']);label=str(g.get('name',ident))
   if not needle or needle in ident or needle in label.casefold():result.append(('/use '+ident+' ',label+' · '+ident))
  return result
 return [(name+' '+key+' ',value) for key,value in ACTIONS.get(name,{}).items() if key.startswith(arg)]

class Completer:
 def __init__(self):self.reset()
 def reset(self):self.items=[];self.index=-1;self.last=None;self.suffix=''
 def tab(self,text,cursor,groups):
  if self.last!=(text,cursor):
   self.items=candidates(text[:cursor],groups);self.index=-1;self.suffix=text[cursor:]
   if len(self.items)!=1:
    self.last=(text,cursor);return text,cursor
  if not self.items:return text,cursor
  self.index=(self.index+1)%len(self.items)
  prefix=self.items[self.index][0];result=prefix+self.suffix
  self.last=(result,len(prefix));return self.last
