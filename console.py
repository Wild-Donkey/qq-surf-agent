#!/usr/bin/env python3
"""Two live log panes and a non-blocking local operations prompt (stdlib only)."""
import fcntl
import argparse,collections,concurrent.futures,json,os,queue,shlex,signal,subprocess,sys,time,unicodedata
try:import curses
except ImportError:curses=None
from pathlib import Path
import agent,qq,workflow,operator_control,service_log,completion,chat_view,avatar_palette,clipboard_image,log_scroll,terminal_mouse
ROOT=Path(__file__).resolve().parent
HELP='''/groups  /use 群名或群号  /say 内容（或直接输入）  /takeover  /resume
/paste  /worker prompt  /jobs  /status  /bot start|restart|stop  /helper status|start|wrap|stop
/help  /quit  · Tab补全命令/群名，F6切换日志窗，↑/↓或滚轮滚动，PgUp/PgDn翻页，End回到实时；退出界面不停止服务'''

def alive(path):
 try:pid=int(path.read_text());os.kill(pid,0);return pid
 except PermissionError:return pid
 except (OSError,ValueError):return None

def spawn(script,*args):
 path=ROOT/'runtime';path.mkdir(exist_ok=True)
 with (path/'console-services.log').open('ab') as out:
  return subprocess.Popen([sys.executable,'-u',str(ROOT/script),*map(str,args)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True).pid

def helper(cfg,api,action):
 work=ROOT/'runtime';pid=alive(work/'qq-helper-service.pid')
 if action=='status':return 'QQ Helper: '+('managed pid='+str(pid) if pid else '当前未由控制台封装')
 if action=='stop':
  if not pid:raise ValueError('没有控制台管理的Helper，不会终止外部实例')
  os.kill(pid,signal.SIGTERM);return '已请求停止受管理的QQ Helper'
 if action not in ('start','wrap'):raise ValueError('helper 支持 status/start/wrap/stop')
 if pid:return 'QQ Helper 已由控制台管理，pid='+str(pid)
 online=False
 try:online=bool(agent.onebot_call(api,'get_status').get('online'))
 except Exception:pass
 if online and action=='start':return '已有QQ连接；使用 /helper wrap 将当前实例切换为持久日志启动'
 if action=='wrap':
  login=agent.onebot_call(api,'get_login_info')
  if str(login.get('user_id'))!=str(cfg['bot_id']):raise ValueError('QQ账号不匹配，未重启')
  output=subprocess.check_output(['ps','-axo','pid=,comm='],text=True)
  matches=[int(line.strip().split(None,1)[0]) for line in output.splitlines() if line.strip().split(None,1)[-1]=='/Applications/QQ.app/Contents/MacOS/QQ']
  if len(matches)!=1:raise ValueError('未发现唯一的QQ主进程，未终止任何实例')
  old=matches[0];os.kill(old,signal.SIGTERM)
  for _ in range(100):
   try:os.kill(old,0)
   except ProcessLookupError:break
   time.sleep(.1)
  else:raise ValueError('原QQ尚未退出，未启动重复实例')
 command=cfg.get('console',{}).get('helper_command',['/Applications/QQ.app/Contents/MacOS/QQ','--no-sandbox','-q',str(cfg['bot_id'])])
 if not isinstance(command,list) or not command or not all(isinstance(v,str) for v in command):raise ValueError('helper_command 必须为参数数组')
 return 'QQ Helper wrapper starting pid='+str(spawn('helper_service.py','--',*command))

class Controller:
 def __init__(self):
  try:self.group=json.loads((ROOT/'runtime/console-state.json').read_text()).get('group')
  except (OSError,ValueError):self.group=None
 def execute(self,line):
  cfg,api,db=qq.load()
  try:
   if not line.startswith('/') or line.startswith('/say '):
    if line.startswith('/say '):line=line[5:]
    if not self.group:raise ValueError('先 /use 选择一个群')
    result=qq.send(db,cfg,api,self.group,line);return '已发送，message_id='+str(result['message_id'])
   command,_,body=line.partition(' ');body=body.strip()
   if command=='/help':return HELP
   if command=='/groups':return '\n'.join(str(g['id'])+' '+g['name'] for g in cfg['groups'] if g.get('enabled',True))
   if command=='/use':
    g=qq.resolve_group(body,cfg);self.group=str(g['id']);(ROOT/'runtime/console-state.json').write_text(json.dumps({'group':self.group}));return '当前群：'+g['name']+' ('+self.group+')'
   if command=='/paste':
    if not self.group:raise ValueError('先 /use 选择聊天目标')
    raw=clipboard_image.read_png();result=qq.send_image(db,cfg,api,self.group,raw);return '截图已发送，message_id='+str(result['message_id'])
   if command=='/takeover':
    if not self.group:raise ValueError('先 /use 选择聊天目标')
    qq.pause(db,self.group,0)
    return '人工发言带【人工接管】标记，按bot owner发言处理；bot保持自动运行，暂停请用 /pause'
   if command in ('/pause','/resume'):
    if not self.group:raise ValueError('先 /use 选择群')
    minutes=0 if command=='/resume' else int(body or 5)
    qq.pause(db,self.group,minutes);service_log.event('surf-bot','operator pause group='+self.group+' minutes='+str(minutes));return '已恢复自动回复' if not minutes else '已暂停自动回复 '+str(minutes)+' 分钟；仍会读取消息'
   if command=='/say':return self.execute(body)
   if command=='/worker':
    if not self.group:raise ValueError('先 /use 选择群')
    return 'worker prompt 已入队：'+operator_control.submit(db,cfg,self.group,body)
   if command=='/jobs':return '\n'.join(str(dict(r)) for r in db.execute('SELECT id,group_id,status,error FROM operator_prompts ORDER BY created DESC LIMIT 10')) or '暂无worker prompt'
   if command=='/status':
    metrics=dict(db.execute("SELECT key,value FROM metrics WHERE key IN ('last_tick','model_state','last_event')"))
    pause=db.execute('SELECT until_time FROM operator_pause WHERE group_id=?',(self.group,)).fetchone()
    return json.dumps({'group':self.group,'paused_seconds':max(0,int(pause[0]-time.time())) if pause else 0,'helper_managed_pid':alive(ROOT/'runtime/qq-helper-service.pid'),'metrics':metrics,'jobs':[dict(r) for r in db.execute("SELECT status,count(*) n FROM social_jobs WHERE status IN ('pending','processing') GROUP BY status")]},ensure_ascii=False,indent=2)
   if command=='/helper':return helper(cfg,api,body or 'status')
   if command=='/bot':
    work=ROOT/'runtime'
    if body=='restart':(work/'workflow.restart').touch();return '已请求重启bot worker'
    if body=='stop':(work/'workflow.stop').touch();return '已请求停止bot服务'
    if body=='start':
     if alive(work/'workflow-supervisor.pid'):return 'bot服务已在运行'
     (work/'workflow.stop').unlink(missing_ok=True)
     return 'bot supervisor starting pid='+str(spawn('supervisor.py'))
    raise ValueError('bot 支持 start/restart/stop')
   raise ValueError('未知命令；/help 查看命令')
  finally:db.close()

class Tail:
 def __init__(self,path):self.path=Path(path);self.identity=None;self.pos=0;self.partial=b''
 def read(self):
  try:stat=self.path.stat()
  except OSError:return []
  ident=(stat.st_dev,stat.st_ino)
  if self.identity!=ident or stat.st_size<self.pos:
   self.pos=max(0,stat.st_size-128000);self.identity=ident;self.partial=b''
  with self.path.open('rb') as f:f.seek(self.pos);raw=f.read(128000);self.pos=f.tell()
  chunks=(self.partial+raw).split(b'\n');self.partial=chunks.pop()[-65536:]
  return [service_log.clean(v.decode('utf-8',errors='replace')) for v in chunks]

def clip(text,width):
 out=[];used=0
 for c in text:
  size=0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W','F') else 1
  if used+size>width:break
  out.append(c);used+=size
 return ''.join(out)

def display_width(text):
 return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in text)

def wrap(text,width):
 if width<=0:return ['']
 lines=[]
 while text:
  part=clip(text,width)
  if not part:break
  lines.append(part);text=text[len(part):]
 return lines or ['']


def run(screen):
 curses.curs_set(1);screen.timeout(100);screen.keypad(True)
 try:curses.mousemask(curses.ALL_MOUSE_EVENTS)
 except curses.error:pass
 sys.stdout.write('\x1b[?1000h\x1b[?1006h');sys.stdout.flush()
 mouse_reader=terminal_mouse.Reader()
 avatars=avatar_palette.AvatarPalette();styles=chat_view.Styles(avatars)
 controller=Controller();buffers=[collections.deque(maxlen=2000),collections.deque(maxlen=2000)];views=[log_scroll.Viewport(True),log_scroll.Viewport()];focus=0
 tails=[Tail(ROOT/'runtime/logs/qq-helper.log'),Tail(ROOT/'runtime/logs/surf-bot.log')]
 # Include supervisor output alongside detailed bot events.
 fallback=Tail(ROOT/'runtime/workflow.log')
 if tails[1].path.exists():fallback.read()  # Don't place stale startup failures after current structured logs.
 outputs=queue.Queue();pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
 text='';cursor=0;history=[];history_index=0;completer=completion.Completer();groups=[];groups_checked=0
 buffers[1].extend(HELP.splitlines())
 if not tails[0].path.exists():buffers[0].append('QQ Helper 尚未封装日志；/helper wrap 接管当前实例，或 /helper start 启动。')
 def task(line):
  try:result=controller.execute(line)
  except Exception as exc:result='操作失败：'+service_log.clean(str(exc))
  outputs.put(result);service_log.event('console',result)
 def put(y,x,value,width,attr=0):
  try:screen.addstr(y,x,clip(value,max(0,width)),attr)
  except curses.error:pass
 try:
  while True:
   for i,tail in enumerate(tails):
    fresh=tail.read();views[i].arrived(fresh);buffers[i].extend(fresh)
   fresh=fallback.read();views[1].arrived(fresh);buffers[1].extend(fresh)
   while not outputs.empty():
    fresh=outputs.get().splitlines();views[1].arrived(fresh);buffers[1].extend(fresh)
   if time.monotonic()-groups_checked>3:
    try:groups=json.loads((ROOT/'workflow.json').read_text()).get('groups',[])
    except (OSError,ValueError):pass
    groups_checked=time.monotonic()
   height,width=screen.getmaxyx();screen.erase()
   if height<12 or width<45:
    put(0,0,'请把终端放大至至少45列、12行。Ctrl-C退出',width-1)
   else:
    pane=height-9;left=(width-1)//2
    for i,title in enumerate(('QQ Helper','surf bot / 操作结果')):
     x=0 if i==0 else left+1;columns=left if i==0 else width-left-2
     visible=views[i].visible(buffers[i],columns,pane)
     put(0,x,('▶ ' if i==focus else '  ')+title+(' [历史 ↑'+str(views[i].offset)+']' if views[i].offset else ' [实时]'),columns)
     for n,(line,group,user) in enumerate(visible):
      attr=styles.attr(chat_view.Row(line,group,user)) if i==0 else 0
      padding=max(0,columns-display_width(line))
      put(1+n,x,line+' '*padding,columns,attr)
    for y in range(pane+1):put(y,left,'│',1)
    put(height-7,0,'当前群 '+str(controller.group or '未选择')+' · 手动发送标记【人工接管】',width-1)
    choices=completer.items if completer.last==(text,cursor) else completion.candidates(text[:cursor],groups)
    index=max(0,completer.index) if completer.last==(text,cursor) else 0
    for row,item in enumerate(choices[index:index+3]):put(height-6+row,0,('→ ' if row==0 else '  ')+item[0]+' · '+item[1],width-1)
    put(height-3,0,'Tab补全 · F6切窗 · ↑↓/滚轮滚动 · PgUp/PgDn翻页 · End实时 · /help · /quit',width-1)
    start=max(0,cursor-(width-6)//2)
    put(height-2,0,'> '+text[start:],width-1)
    try:screen.move(height-2,min(width-2,2+display_width(text[start:cursor])))
    except curses.error:pass
   screen.refresh()
   key=mouse_reader.get(screen)
   if key is None:continue
   if isinstance(key,terminal_mouse.Mouse):
    if 0<=key.y<height-8 and 0<=key.x<width:
     focus=0 if key.x<(width-1)//2 else 1
     if key.delta:views[focus].scroll(key.delta)
    continue
   if key!='\t':completer.reset()
   if key in ('\n','\r',curses.KEY_ENTER):
    line=text.strip();text='';cursor=0
    if line in ('/quit','/exit'):break
    if line:
     history.append(line);history_index=len(history);views[1].arrived(['> '+service_log.clean(line)]);buffers[1].append('> '+service_log.clean(line));pool.submit(task,line)
   elif key=='\t':text,cursor=completer.tab(text,cursor,groups)
   elif key==curses.KEY_F6:focus=1-focus
   elif key==curses.KEY_UP:views[focus].scroll(1)
   elif key==curses.KEY_DOWN:views[focus].scroll(-1)
   elif key==curses.KEY_PPAGE:views[focus].scroll(max(1,height-10))
   elif key==curses.KEY_NPAGE:views[focus].scroll(-max(1,height-10))
   elif key==curses.KEY_END:views[focus].live()
   elif key==curses.KEY_MOUSE:
    try:
     _,mx,my,_,buttons=curses.getmouse()
     if my<height-8:
      focus=0 if mx<(width-1)//2 else 1
      if buttons & getattr(curses,'BUTTON4_PRESSED',0):views[focus].scroll(3)
      elif buttons & getattr(curses,'BUTTON5_PRESSED',0):views[focus].scroll(-3)
    except curses.error:pass
   elif key in (curses.KEY_BACKSPACE,'\x7f','\b'):
    if cursor:text=text[:cursor-1]+text[cursor:];cursor-=1
   elif key==curses.KEY_DC:text=text[:cursor]+text[cursor+1:]
   elif key==curses.KEY_LEFT:cursor=max(0,cursor-1)
   elif key==curses.KEY_RIGHT:cursor=min(len(text),cursor+1)
   elif key==curses.KEY_HOME:cursor=0
   elif key in ('\x10','\x0e'):
    history_index=max(0,min(len(history),history_index+(-1 if key=='\x10' else 1)));text=history[history_index] if history_index<len(history) else '';cursor=len(text)
   elif key=='\x15':text='';cursor=0
   elif isinstance(key,str) and key.isprintable() and len(text)<12000:text=text[:cursor]+key+text[cursor:];cursor+=1
 except KeyboardInterrupt:pass
 finally:
  sys.stdout.write('\x1b[?1006l\x1b[?1000l');sys.stdout.flush()
  avatars.close();pool.shutdown(wait=False,cancel_futures=True)

def console_lock():
 path=ROOT/'runtime'/'console.lock';path.parent.mkdir(parents=True,exist_ok=True)
 lock=path.open('a+')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  lock.close();raise SystemExit('控制台已在运行，请使用已有窗口。')
 lock.seek(0);lock.truncate();lock.write(str(os.getpid()));lock.flush()
 return lock

def lite_default(args):
 if args.full:return False
 if args.lite:return True
 try:return json.loads((ROOT/'workflow.json').read_text()).get('console',{}).get('default_mode','lite')=='lite'
 except (OSError,ValueError):return True


def main():
 p=argparse.ArgumentParser(description='QQ surf terminal console');mode=p.add_mutually_exclusive_group();mode.add_argument('--full',action='store_true',help='使用双窗彩色完整界面');mode.add_argument('--lite',action='store_true',help='逐行纯文本模式，只显示简化聊天，不加载颜色和bot日志');p.add_argument('--command',help='执行一条控制台命令后退出');p.add_argument('--helper-wrap',action='store_true',help='将当前QQ实例重启为持久日志启动');args=p.parse_args()
 if args.command:print(Controller().execute(args.command));return
 with console_lock():
  if args.helper_wrap:print(Controller().execute('/helper wrap'))
  if lite_default(args):
   import lite_console
   lite_console.run();return
  if not sys.stdin.isatty():raise SystemExit('交互模式需要终端；自动化请用 --command')
  if curses is None:raise SystemExit('当前Python未提供curses，请使用 --lite')
  curses.wrapper(run)
if __name__=='__main__':main()
