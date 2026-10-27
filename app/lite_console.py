"""Plain single-stream terminal with concurrent prompt redraw."""
import collections,json,sys,threading,time,os
from pathlib import Path
from app import chat_view,completion,service_log
ROOT=Path(__file__).resolve().parents[1]

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

def print_chat_batch(output,pending,reading):
 # Empty two-character prompt: erase in place without adding a scrollback line.
 if reading and getattr(output,'isatty',lambda:False)():output.write('\r  \r')
 while pending:output.write(pending.popleft()+'\n')
 if reading:output.write('> ')
 output.flush()

def make_completer(root):
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/deps'))
 from prompt_toolkit.completion import Completer,Completion
 class Groups(Completer):
  def get_completions(self,document,event):
   prefix=document.text_before_cursor
   try:groups=json.loads((root/'workflow.json').read_text()).get('groups',[])
   except (OSError,ValueError):groups=[]
   for value,label in completion.candidates(prefix,groups):yield Completion(value,start_position=-len(prefix),display=value,display_meta=label)
 return Groups()


def run(controller=None,input_fn=None,output=None):
 output=output or sys.stdout
 if input_fn is None and sys.stdin.isatty() and output.isatty() and os.environ.get('TERM')!='dumb':
  # 1) urwid (highest terminal compatibility, monochrome, full-screen)
  try:
   from app.lite_view_urwid import run as run_urwid
  except ImportError:run_urwid=None
  if run_urwid:
   return run_urwid(controller,ROOT,None)
  # 2) prompt_toolkit (legacy full-screen lite)
  try:
   completer=make_completer(ROOT)
   from app.lite_view import run as run_view
  except ImportError:run_view=None
  if run_view:
   output.write('\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1007l\x1b[?1049l');output.flush()
   return run_view(controller,ROOT,completer)
 return _run(controller,input_fn,output)

def _run(controller=None,input_fn=None,output=None,managed_prompt=False):
 from app.console import Controller
 from app.terminal_feed import Feed
 controller=controller or Controller();input_fn=input_fn or input;output=output or sys.stdout
 try:
  if managed_prompt:raise ImportError
  import readline
  old_completer=readline.get_completer();old_delims=readline.get_completer_delims();setup_completion(readline)
 except ImportError:readline=None
 tail=Feed(ROOT);stop=threading.Event();printing=threading.Lock();reading=threading.Event()
 def write(text):
  with printing:output.write(text+'\n');output.flush()
 initial=[chat_view.parse(line).text for line in tail.read()]
 write('Luna lite · QQ / 应用 / 理解 · /use 选择群 · /help 帮助 · /quit 退出（后台继续运行）')
 for line in initial[-40:]:write(line)
 def follow():
  pending=collections.deque(maxlen=2000)
  while not stop.wait(.25):
   try:pending.extend(chat_view.parse(line).text for line in tail.read())
   except OSError as exc:
    service_log.event('console','chat log tail retry error='+type(exc).__name__)
    continue
   # With readline, don't break a half-typed command. Flush after it is submitted.
   if not managed_prompt and reading.is_set() and readline and readline.get_line_buffer():continue
   if pending:
    with printing:
     if managed_prompt:
      while pending:output.write(pending.popleft()+'\n')
      output.flush()
     else:print_chat_batch(output,pending,reading.is_set())
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
    write('Tab补全；实时聊天不会打断输入。鼠标滚轮使用终端原生回滚；Ctrl-P/Ctrl-N翻命令历史。');continue
   try:result=controller.execute(line)
   except Exception as exc:result='操作失败：'+service_log.clean(str(exc))
   write(service_log.clean(result));service_log.event('console',result)
 finally:
  stop.set();thread.join(timeout=1)
  if readline:
   readline.set_completer(old_completer);readline.set_completer_delims(old_delims)
