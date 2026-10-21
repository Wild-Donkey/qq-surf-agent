"""Conservative application guard against repeating a peer's recent phrasing."""
import difflib,re,unicodedata

def plain(text):return ''.join(c.lower() for c in str(text) if unicodedata.category(c)[0] in ('L','N'))
FILLER=re.compile('所以|看来|确实|只能|还是|那就|那|这|也|就|了|吧|啊|呢|的|是')

def repeats_peer(decision,payload):
 if decision.get('action')!='reply' or payload.get('pending_requests') or payload.get('operator_prompts'):return False
 if decision.get('emoticon'):return False
 parts=decision.get('reply_parts')
 if parts and any(part.get('emoticon') for part in parts if isinstance(part,dict)):return False
 text=''.join(str(p.get('text','')) for p in parts if isinstance(p,dict)) if parts else decision.get('text','')
 candidate=plain(text)
 if len(candidate)<12:return False
 speakers={};seen=set()
 for row in payload.get('recent_target_messages',[])+payload.get('unread_messages',[]):
  user=str(row.get('speaker',row.get('user_id','')))
  if not user or user=='self' or row.get('author_role')=='self':continue
  source=plain(row.get('text',''))
  key=(user,source)
  if not source or key in seen:continue
  seen.add(key);speakers.setdefault(user,[]).append(source[-160:])
 for messages in speakers.values():
  source=''.join(messages[-6:])[-700:]
  if len(source)<8:continue
  blocks=difflib.SequenceMatcher(None,candidate,source,autojunk=False).get_matching_blocks()
  covered=set(i for block in blocks for i in range(block.a,block.a+block.size))
  novel=''.join(c for i,c in enumerate(candidate) if i not in covered)
  if len(covered)/len(candidate)>=.75 and len(FILLER.sub('',novel))<=2:return True
 return False
