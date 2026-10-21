"""Plain line-oriented terminal: chat only, no curses, ANSI, colors or avatars."""
import collections,json,sys,threading,time
from pathlib import Path
import chat_view,completion,service_log
ROOT=Path(__file__).resolve().parent

def chat_lines(lines):
 result=[]
 for line in lines:
  row=chat_view.parse(service_log.clean(line))
  if row.group:result.append(row.text)
 return result

def setup_completion(readline):
 def complete(text,state):
  prefix=readline.get_line_buffer()[:readline.get_endidx()]
  try:groups=json.loads((ROOT/'workflow.json').read_text()).get('groups',[])
  except (OSError,ValueError):groups=[]
  choices=[value for value,_ in completion.candidates(prefix,groups)]
  return choices[state] if state<len(choices) else None
 readline.set_completer_delims('');readline.set_completer(complete)
 if 'libedit' in (readline.__doc__ or ''):readline.parse_and_bind('bind ^I rl_complete')
 else:readline.parse_and_bind('tab: complete')

def run(controller=None,input_fn=None,output=None):
 from console import Controller,Tail
 controller=controller or Controller();input_fn=input_fn or input;output=output or sys.stdout
 try:
  import readline
  old_completer=readline.get_completer();old_delims=readline.get_completer_delims();setup_completion(readline)
 except ImportError:readline=None
 tail=Tail(ROOT/'runtime/logs/qq-helper.log');stop=threading.Event();printing=threading.Lock();reading=threading.Event()
 def write(text):
  with printing:output.write(text+'\n');output.flush()
 initial=chat_lines(tail.read())
 write('Luna lite · 仅聊天 · /use 选择群 · /help 帮助 · /quit 退出（后台继续运行）')
 for line in initial[-40:]:write(line)
 def follow():
  pending=collections.deque(maxlen=2000)
  while not stop.wait(.25):
   pending.extend(chat_lines(tail.read()))
   # With readline, don't break a half-typed command. Flush after it is submitted.
   if reading.is_set() and readline and readline.get_line_buffer():continue
   if pending:
    with printing:
     if reading.is_set():output.write('\n')
     while pending:output.write(pending.popleft()+'\n')
     if reading.is_set():output.write('> ')
     output.flush()
 thread=threading.Thread(target=follow,name='lite-chat-log',daemon=True);thread.start()
 try:
  while True:
   reading.set()
   try:line=input_fn('> ').strip()
   except (EOFError,KeyboardInterrupt):write('已退出 lite；后台服务继续运行。');break
   finally:reading.clear()
   if line in ('/quit','/exit'):break
   if not line:continue
   if line=='/help':
    write('\n'.join(key+' · '+value for key,value in completion.COMMANDS.items()))
    write('Tab补全；终端支持时再次Tab显示候选。输入命令期间暂缓滚动，提交后继续。');continue
   try:result=controller.execute(line)
   except Exception as exc:result='操作失败：'+service_log.clean(str(exc))
   write(service_log.clean(result));service_log.event('console',result)
 finally:
  stop.set();thread.join(timeout=1)
  if readline:
   readline.set_completer(old_completer);readline.set_completer_delims(old_delims)
