"""Scroll by displayed rows, preserving a historical view while new logs arrive."""
import functools
import chat_view

def chunks(text,width):
 import unicodedata
 result=[];part='';used=0
 for char in text:
  size=0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ('W','F') else 1
  if used+size>width and part:result.append(part);part='';used=0
  part+=char;used+=size
 if part or not result:result.append(part)
 return result

@functools.lru_cache(maxsize=4096)
def rows(line,width,simplify):
 row=chat_view.parse(line) if simplify else chat_view.Row(line)
 return tuple((part,row.group,row.user) for part in chunks(row.text,max(1,width)))

class Viewport:
 def __init__(self,simplify=False):self.offset=0;self.width=40;self.simplify=simplify
 def arrived(self,lines):
  if self.offset:self.offset+=sum(len(rows(line,self.width,self.simplify)) for line in lines)
 def visible(self,lines,width,height):
  self.width=max(1,width);height=max(1,height)
  visual=[part for line in lines for part in rows(line,self.width,self.simplify)]
  self.offset=min(self.offset,max(0,len(visual)-height))
  end=len(visual)-self.offset
  return visual[max(0,end-height):end]
 def scroll(self,delta):self.offset=max(0,self.offset+delta)
 def live(self):self.offset=0
