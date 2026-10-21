"""Application-owned reply forms: text, one reaction, or an ordered mixed message."""
import emoticons

CAPABILITIES={'decision_order':['是否需要回应','选择文字、单个表情、组合回复','无合适回应才wait'],
 'text':{'action':'reply','text':'内容'},'reaction':{'action':'reply','text':'','emoticon':{'kind':'face或sticker','id':'应用目录ID'}},
 'sequence':{'action':'reply','reply_parts':[{'text':'可选文字'},{'emoticon':{'kind':'face或sticker','id':'应用目录ID'}}]},
 'limits':{'max_parts':8,'max_emoticons':4,'max_text_chars':1200},'delivery':'组合按原序组成一条QQ消息，一次发送回执；不随机配表情'}

def prepare(cfg,group,parts,explicit=False):
 if not isinstance(parts,list) or not 1<=len(parts)<=8:raise ValueError('invalid_reply_parts')
 result=[];characters=0;reactions=0
 for part in parts:
  if not isinstance(part,dict) or set(part) not in ({'text'},{'emoticon'}):raise ValueError('invalid_reply_part')
  if 'text' in part:
   text=part['text']
   if not isinstance(text,str) or not text.strip():raise ValueError('invalid_reply_part_text')
   characters+=len(text);result.append({'text':text})
  else:
   selected=emoticons.safe_selection(cfg['context_dir'],group,part['emoticon'])
   if not selected:raise ValueError('unavailable_reply_part_emoticon')
   if selected['kind']=='sticker' and not explicit and emoticons.recent_echo(cfg['context_dir'],group,selected['id']):raise ValueError('recent_reply_part_emoticon')
   reactions+=1;result.append({'emoticon':selected})
 if characters>1200 or reactions>4:raise ValueError('reply_parts_too_long')
 return result

def segments(root,group,parts):
 result=[]
 for part in parts:
  if 'text' in part:result.append({'type':'text','data':{'text':part['text']}})
  else:result.extend(emoticons.segments(root,group,part['emoticon']))
 return result

def selections(parts):return [part['emoticon'] for part in parts if 'emoticon' in part]
