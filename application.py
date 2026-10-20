"""Worker-facing application API. Workers submit state through validated APIs; application owns files, profiles and policy."""
import json,time,hashlib,re
from pathlib import Path
from memory import MarkdownMemory,atomic_write,safe_id
import archive,emoticons
TOOLS=[
 {'name':'update_states','description':'提交群状态 group_state 和成员状态 members:[{user_id,state}]，应用校验后落盘'},
 {'name':'finish_turn','description':'一次提交状态并结束：group_state,members,action(wait/reply/share),text,candidate_id,emoticon,reason'},
 {'name':'read_context','description':'读取应用准备的当前群上下文，无参数'},
 {'name':'get_member','description':'读取当前上下文内成员画像，参数 user_id'},
 {'name':'search_history','description':'按 query 检索当前群已落盘历史'},
 {'name':'list_emoticons','description':'读取本群可用表情目录'},
 {'name':'reply','description':'回复当前群，参数 text 和可选 emoticon'},
 {'name':'share','description':'分享给定 candidate_id，参数 candidate_id,text'},
 {'name':'wait','description':'保持安静，参数 reason'}]
READS={'update_states','read_context','get_member','search_history','list_emoticons'}
def decode(raw):
 value=json.loads(raw)
 if not isinstance(value,dict) or set(value)-{'call'}:raise ValueError('worker_must_call_application_api')
 call=value.get('call')
 if not isinstance(call,dict) or set(call)-{'name','arguments'}:raise ValueError('invalid_api_call')
 if call.get('name') not in {t['name'] for t in TOOLS}:raise ValueError('api_not_exposed')
 args=call.get('arguments',{})
 if not isinstance(args,dict):raise ValueError('invalid_api_arguments')
 name=call['name'];allowed={'read_context':set(),'get_member':{'user_id'},'search_history':{'query'},'list_emoticons':set(),'reply':{'text','emoticon'},'share':{'candidate_id','text'},'wait':{'reason'},'update_states':{'group_state','members','images'},'finish_turn':{'group_state','members','images','action','text','candidate_id','emoticon','reason'}}[name]
 if set(args)-allowed:raise ValueError('argument_not_exposed')
 if name in ('reply','share') and not isinstance(args.get('text',''),str):raise ValueError('invalid_text')
 return {'name':name,'arguments':args}
def decision(call):
 name=call['name'];args=call['arguments']
 if name=='finish_turn':
  if args.get('action') not in ('wait','reply','share'):raise ValueError('invalid_finish_action')
  if not isinstance(args.get('text',''),str):raise ValueError('invalid_text')
  return {'action':args['action'],'text':args.get('text','')[:180],'candidate_id':args.get('candidate_id'),'emoticon':args.get('emoticon'),'reason':str(args.get('reason',''))[:200],'state_updates':{k:args[k] for k in ('group_state','members','images') if k in args}}
 if name in READS:return {'action':'read','call':call,'text':''}
 return {'action':name,'text':args.get('text','')[:180],'candidate_id':args.get('candidate_id'),'emoticon':args.get('emoticon'),'reason':str(args.get('reason',''))[:200]}
def read(db,cfg,target,payload,call):
 name=call['name'];args=call['arguments']
 if name=='update_states':
  update_states(cfg,target,payload,args)
  return {'saved':True,'member_memories':member_context(cfg,target,payload),'group_state':group_state(cfg,target)}
 if name=='read_context':return {k:v for k,v in payload.items() if k not in ('_vision','_attached_images','tool_results')}
 if name=='list_emoticons':return emoticons.catalog(cfg['context_dir'],target)
 if name=='get_member':
  user=str(args.get('user_id',''))
  allowed={str(v['id']) for v in payload.get('member_profiles',[])}
  if user not in allowed:raise ValueError('member_outside_context')
  row=db.execute('SELECT * FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
  result=dict(row) if row else {'user_id':user,'kind':'unknown'}
  result['memory']=member_markdown(cfg,target,user)
  return result
 if name=='search_history':
  query=args.get('query','')
  if not isinstance(query,str) or not query.strip() or len(query)>300:raise ValueError('invalid_history_query')
  disk=archive.Archive(cfg['context_dir'])
  try:return disk.search(target,query,limit=12)
  finally:disk.close()
 raise ValueError('not_read_api')
def settle_memory(memory,group,batch,views):
 # Raw episode bookkeeping is automatic; semantic state comes through update_states.
 summary='；'.join(str(v['speaker'])+'：'+v['text'][:160] for v in views)[:1600]
 state=memory.conversation_state(group) or ('近期原话摘录（非语义结论，原文可按历史接口查阅）：\n'+summary)
 memory.remember_window(group,batch,state[-2800:],summary)
 return summary


def group_state(cfg,group):
    memory=MarkdownMemory(cfg['context_dir'])
    try:return memory.conversation_state(group)
    finally:memory.close()


def member_markdown(cfg,group,user):
    path=Path(cfg['context_dir'])/'people'/safe_id(user)/'people.md'
    if not path.exists():return ''
    text=path.read_text()
    # Group-specific state from other groups is never injected into this worker.
    def keep(match):return match[0] if match[1]==str(group) else ''
    return re.sub(r'<!-- group-state:(\d+):start -->.*?<!-- group-state:\1:end -->',keep,text,flags=re.S)


def member_context(cfg,group,payload,budget=16000):
    profiles=payload.get('member_profiles',[]);result=[]
    for profile in profiles:
        user=str(profile['id']);full=member_markdown(cfg,group,user)
        text=full[:min(2000,max(0,budget))];budget-=len(text)
        result.append({'user_id':user,'markdown':text,'truncated':len(text)<len(full),'profile':profile['current_judgment']})
    return result


def update_states(cfg,group,payload,args):
    allowed={str(p['id']) for p in payload.get('member_profiles',[])}
    members=args.get('members',[]);state=args.get('group_state');images=args.get('images',[])
    available={v['reference']:v for v in payload.get('_attached_images',[])}
    if not isinstance(images,list) or len(images)>4:raise ValueError('invalid_image_states')
    for image in images:
        if not isinstance(image,dict) or set(image)!={'reference','description'} or image['reference'] not in available:raise ValueError('image_not_attached')
        if not isinstance(image['description'],str) or len(image['description'])>1200:raise ValueError('invalid_image_description')
    if not isinstance(members,list) or len(members)>60:raise ValueError('invalid_member_states')
    if state is not None and (not isinstance(state,str) or len(state)>2800):raise ValueError('invalid_group_state')
    # Validate the entire request before writing any file.
    for item in members:
        if not isinstance(item,dict) or set(item)!={'user_id','state'} or str(item['user_id']) not in allowed:raise ValueError('member_outside_context')
        if not isinstance(item['state'],str) or len(item['state'])>3000:raise ValueError('invalid_member_state')
    for image in images:
        meta=available[image['reference']]
        if meta.get('event_key'):archive.record_visual(cfg,meta['event_key'],image['description'],meta.get('media_key',''))
        if meta.get('media_key'):emoticons.learn(cfg['context_dir'],meta['group_id'],meta['url'],image['description'],meta['media_key'])
    for item in members:
        user=str(item['user_id']);path=Path(cfg['context_dir'])/'people'/safe_id(user)/'people.md'
        old=path.read_text() if path.exists() else '# 成员记忆\n'
        start='<!-- group-state:'+str(group)+':start -->';end='<!-- group-state:'+str(group)+':end -->'
        body=item['state'].replace('<!--','&lt;!--').replace('-->','--&gt;')
        block=start+'\n## 本群状态 '+str(group)+'\n\n'+body+'\n'+end
        if start in old and end in old:
            before,rest=old.split(start,1);_,after=rest.split(end,1);new=before+block+after
        else:new=old.rstrip()+'\n\n'+block+'\n'
        atomic_write(path,new)
    if state is not None:
        memory=MarkdownMemory(cfg['context_dir']);stamp=time.time()
        try:memory.remember_window(group,[{'event_key':'api:'+hashlib.sha256(state.encode()).hexdigest(),'created':stamp}],state,'')
        finally:memory.close()
