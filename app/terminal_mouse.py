"""SGR mouse fallback for macOS ncurses builds lacking BUTTON5_PRESSED.
Protocol: https://invisible-island.net/xterm/ctlseqs/ctlseqs.html#h2-Mouse-Tracking
"""
import re
from dataclasses import dataclass
@dataclass(frozen=True)
class Mouse:
 x:int;y:int;delta:int=0;click:bool=False

def decode(packet):
 m=re.fullmatch(r'\x1b\[<(\d{1,7});(\d{1,7});(\d{1,7})([Mm])',packet)
 if not m:return None
 button,x,y=map(int,m.groups()[:3])
 if x<1 or y<1:return None
 base=button & ~(4|8|16)
 delta=(3 if base==64 else -3 if base==65 else 0) if m[4]=='M' else 0
 return Mouse(x-1,y-1,delta,base==0 and m[4]=='M')

class Reader:
 def __init__(self):self.pending=''
 def get(self,screen):
  import curses
  while True:
   try:key=screen.get_wch()
   except curses.error:return None
   if not isinstance(key,str):self.pending='';return key
   if not self.pending:
    if key=='\x1b':self.pending=key;continue
    return key
   self.pending+=key;value=self.pending
   if value in ('\x1b[','\x1b[<'):continue
   if len(value)==2:
    if key=='\x1b':self.pending=key;continue
    self.pending='';return key
   if value.startswith('\x1b[<'):
    if key in 'Mm':self.pending='';return decode(value)
    if len(value)<=64 and all(c.isdigit() or c==';' for c in value[3:]):continue
    self.pending='';return None
   # Consume unsupported CSI sequences in full; never paste their digits into input.
   if len(value)>64 or '@'<=key<='~':self.pending='';return None
