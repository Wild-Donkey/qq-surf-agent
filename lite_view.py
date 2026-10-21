"""Single-column, monochrome lite view with application-owned scrollback."""
import collections,asyncio
from log_scroll import Viewport

class History:
 def __init__(self):self.lines=collections.deque(maxlen=2000);self.view=Viewport();self.new=0
 def append(self,lines):
  if self.view.offset:self.new+=len(lines)
  self.view.arrived(lines);self.lines.extend(lines)
 def visible(self,width,height):
  result=self.view.visible(list(self.lines),width,height)
  if not self.view.offset:self.new=0
  return result
 def scroll(self,delta):
  self.view.scroll(delta)
  if not self.view.offset:self.new=0
 def live(self):self.view.live();self.new=0

def run(controller,root,completer):
 from prompt_toolkit import Application
 from prompt_toolkit.layout import Layout,HSplit,Window
 from prompt_toolkit.layout.controls import FormattedTextControl
 from prompt_toolkit.widgets import TextArea
 from prompt_toolkit.key_binding import KeyBindings
 from prompt_toolkit.filters import has_completions
 from prompt_toolkit.output import ColorDepth
 from prompt_toolkit.history import InMemoryHistory
 from prompt_toolkit.mouse_events import MouseEventType
 import lite_console,completion,service_log,chat_view
 from console import Controller
 from terminal_feed import Feed
 controller=controller or Controller();history=History();tail=Feed(root);bindings=KeyBindings()
 def scroll(amount):history.scroll(amount);app.invalidate()
 def mouse(event):
  if event.event_type==MouseEventType.SCROLL_UP:scroll(3);return None
  if event.event_type==MouseEventType.SCROLL_DOWN:scroll(-3);return None
  return NotImplemented
 class ScrollControl(FormattedTextControl):
  def mouse_handler(self,event):
   if event.event_type in (MouseEventType.SCROLL_UP,MouseEventType.SCROLL_DOWN):return mouse(event)
   return super().mouse_handler(event)
 def rows():
  size=app.output.get_size();height=chat.render_info.window_height if chat.render_info else max(1,size.rows-2)
  return [('',line+'\n',mouse) for line,_,_ in history.visible(size.columns,height)] or [('', '等待聊天记录…',mouse)]
 chat=Window(ScrollControl(rows),wrap_lines=False,always_hide_cursor=True)
 def status():
  if history.view.offset:return '历史 ↑'+str(history.view.offset)+'行 · 新增'+str(history.new)+'条 · End回实时'
  return '实时 · 滚轮/↑↓/PgUp/PgDn滚动 · End回实时 · Tab补全 · /quit退出'
 status_bar=Window(ScrollControl(status),height=1)
 command_lock=asyncio.Lock()
 async def execute(line):
  async with command_lock:await execute_serial(line)
 async def execute_serial(line):
  if line=='/help':result='\n'.join(k+' · '+v for k,v in completion.COMMANDS.items())
  else:
   try:result=await asyncio.to_thread(controller.execute,line)
   except Exception as exc:result='操作失败：'+service_log.clean(str(exc))
  history.append(service_log.clean(result).splitlines());service_log.event('console',result);app.invalidate()
 def accept(buffer):
  line=buffer.text.strip()
  if line in ('/quit','/exit'):app.exit();return False
  if line:app.create_background_task(execute(line))
  return False
 entry=TextArea(height=1,prompt='> ',multiline=False,wrap_lines=False,completer=completer,
                complete_while_typing=False,history=InMemoryHistory(),accept_handler=accept)
 original_mouse=entry.control.mouse_handler
 def entry_mouse(event):
  if event.event_type in (MouseEventType.SCROLL_UP,MouseEventType.SCROLL_DOWN):return mouse(event)
  return original_mouse(event)
 entry.control.mouse_handler=entry_mouse
 @bindings.add('pageup')
 def pageup(event):scroll(max(1,(chat.render_info.window_height if chat.render_info else 10)-1))
 @bindings.add('pagedown')
 def pagedown(event):scroll(-max(1,(chat.render_info.window_height if chat.render_info else 10)-1))
 @bindings.add('up',filter=~has_completions)
 def up(event):scroll(1)
 @bindings.add('down',filter=~has_completions)
 def down(event):scroll(-1)
 @bindings.add('end')
 def end(event):history.live();app.invalidate()
 @bindings.add('c-c')
 def quit_or_clear(event):
  if entry.text:entry.text=''
  else:app.exit()
 async def follow():
  while True:
   await asyncio.sleep(.25)
   try:lines=[chat_view.parse(line).text for line in tail.read()]
   except OSError as exc:service_log.event('console','lite tail retry error='+type(exc).__name__);continue
   if lines:history.append(lines);app.invalidate()
 def start():
  history.append([chat_view.parse(line).text for line in tail.read()][-2000:]);app.create_background_task(follow())
 app=Application(layout=Layout(HSplit([chat,status_bar,entry]),focused_element=entry),key_bindings=bindings,
                 full_screen=True,mouse_support=True,color_depth=ColorDepth.DEPTH_1_BIT)
 try:app.run(pre_run=start)
 except (EOFError,KeyboardInterrupt):pass
