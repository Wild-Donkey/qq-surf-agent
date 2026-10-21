"""Format raw Helper lines without changing durable original logs."""
import colorsys,hashlib,re,functools
try:import curses
except ImportError:curses=None
from dataclasses import dataclass
STAMP=r'\d{2}-\d{2} \d{2}:\d{2}:\d{2}'
PREFIX=re.compile(r'^(?P<time>'+STAMP+r')(?:\s+'+STAMP+r')?\s*(?:\[(?P<level>\w+)\]\s*[^|]*\|\s*)?(?P<body>.*)$')
GROUP=re.compile(r'(?P<direction>接收 <-|发送 ->) 群聊 \[(?P<group>.*?)\((?P<gid>\d+)\)\] \[(?P<name>.*?)\((?P<uid>\d+)\)\]\s*(?P<text>.*)')
PRIVATE=re.compile(r'(?P<direction>接收 <-|发送 ->) 私聊(?: \[(?P<name>.*?)\((?P<uid>\d+)\)\]| \((?P<peer>\d+)\))\s*(?P<text>.*)')
@dataclass
class Row:
 text:str;group:str='';user:str=''

def shorten(name):
 # Five visible characters; combining marks and variation selectors stay attached.
 import unicodedata
 result='';count=0
 for ch in name:
  if not unicodedata.combining(ch) and ch not in ('\ufe0f','\u200d'):
   if count==5:break
   count+=1
  result+=ch
 return result

def parse(line):
 match=PREFIX.match(line)
 if not match:return Row(line)
 stamp=match['time'][6:];body=match['body'];m=GROUP.fullmatch(body)
 if m:
  text=re.sub(r'(@[^@\n]+?)\s+\(\d{5,12}\)',r'\1',m['text'])
  return Row(stamp+' '+('←' if m['direction']=='接收 <-' else '→')+' ['+shorten(m['group'])+'] '+shorten(m['name'])+'：'+text,m['gid'],m['uid'])
 m=PRIVATE.fullmatch(body)
 if m:return Row(stamp+' '+('←' if m['direction']=='接收 <-' else '→')+' [私聊] '+shorten(m['name'] or '好友')+'：'+m['text'],'private',m['uid'] or m['peer'])
 level=match['level'];return Row(stamp+' '+('['+level+'] ' if level and level!='info' else '')+body)

@functools.lru_cache(maxsize=256)
def rgb(index):
 basic=[(0,0,0),(128,0,0),(0,128,0),(128,128,0),(0,0,128),(128,0,128),(0,128,128),(192,192,192),(128,128,128),(255,0,0),(0,255,0),(255,255,0),(0,0,255),(255,0,255),(0,255,255),(255,255,255)]
 if index<16:return basic[index]
 if index>=232:return (8+(index-232)*10,)*3
 n=index-16;cube=[0,95,135,175,215,255];return cube[n//36],cube[n//6%6],cube[n%6]
@functools.lru_cache(maxsize=2048)
def luminance(color):
 def linear(v):v=v/255;return v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4
 return sum(v*linear(c) for v,c in zip((.2126,.7152,.0722),color))
def contrast(a,b):
 x,y=sorted((luminance(a),luminance(b)));return (y+.05)/(x+.05)
def distance(a,b):return sum((x-y)**2 for x,y in zip(a,b))

class Styles:
 def __init__(self,avatars):
  self.avatars=avatars;self.pairs={};self.backgrounds={};self.colors=0
  if curses is None:return
  try:
   if curses.has_colors():curses.start_color();self.colors=curses.COLORS
  except curses.error:pass
 def attr(self,row):
  if self.colors<8 or not row.group:return 0
  limit=min(self.colors,256);indices=list(range(limit));source=self.avatars.color('group',row.group)
  h,s,_=colorsys.rgb_to_hsv(*(v/255 for v in source))
  if s<.1:h=int(hashlib.sha256(row.group.encode()).hexdigest()[:8],16)/0xffffffff
  dark=tuple(int(v*255) for v in colorsys.hsv_to_rgb(h,max(.35,s),.30))
  pool=[i for i in indices if luminance(rgb(i))<.12 and max(rgb(i))-min(rgb(i))>=50]
  used={bg for key,bg in self.backgrounds.items() if key!=row.group}
  bg=min((i for i in pool if i not in used),key=lambda i:distance(rgb(i),dark),default=min(pool,key=lambda i:distance(rgb(i),dark)))
  self.backgrounds[row.group]=bg
  person=self.avatars.color('user',row.user or '0');h,s,v=colorsys.rgb_to_hsv(*(v/255 for v in person))
  if s<.1:h=int(hashlib.sha256(row.user.encode()).hexdigest()[:8],16)/0xffffffff;s=.5
  light=tuple(int(x*255) for x in colorsys.hsv_to_rgb(h,min(.5,s),1))
  choices=[i for i in indices if contrast(rgb(i),rgb(bg))>=4.5]
  fg=min(choices,key=lambda i:distance(rgb(i),light))
  pair=(fg,bg)
  if pair not in self.pairs:
   if len(self.pairs)>=min(curses.COLOR_PAIRS-1,255):return 0
   ident=len(self.pairs)+1
   try:curses.init_pair(ident,fg,bg)
   except curses.error:return 0
   self.pairs[pair]=ident
  return curses.color_pair(self.pairs[pair])
