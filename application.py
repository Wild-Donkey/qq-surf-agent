"""Worker-facing application API. Workers submit state through validated APIs; application owns files, profiles and policy."""
import json,time,hashlib,re
from pathlib import Path
from memory import MarkdownMemory,atomic_write,safe_id
import archive,emoticons,participants
from protocol import parse_json
import memory_store
TOOLS=[
 {'name':'get_memory_schema','description':'读取群/成员状态与画像的规范字段，不接受任意字段'},
 {'name':'update_states','description':'提交群状态 group_state 和成员状态 members:[{user_id,state}]，应用校验后落盘'},
 {'name':'finish_turn','description':'一次提交状态并结束：group_state,members,action(wait/reply/share/merged_forward),text,candidate_id,bundle_id,message_ids,quality,emoticon,reason'},
 {'name':'read_context','description':'读取应用准备的当前群上下文，无参数'},
 {'name':'get_member','description':'读取当前上下文内成员画像，参数 user_id'},
 {'name':'search_history','description':'按 query 检索当前群已落盘历史'},
 {'name':'search_emoticons','description':'按语义描述/标签检索本群已收集表情，参数query，可选include_unreviewed查看尚未理解的图片'},
 {'name':'view_emoticon','description':'查看本群收集的某张表情，参数id；应用下一次调用附上真实图片'},
 {'name':'list_emoticons','description':'读取本群可用表情目录'},
 {'name':'reply','description':'回复当前群，参数 text 和可选 emoticon'},
 {'name':'share','description':'分享给定 candidate_id，参数 candidate_id,text'},
 {'name':'merged_forward','description':'合并转发一段节目：bundle_id、message_ids、quality=natural；搬本群节目到其他活跃群时再带 outgoing_moments.destination.route_id，省略route_id则搬入当前群'},
 {'name':'forward','description':'转发当前群中某条消息（原生转发卡片），参数 message_id（取自 recent_target_messages）'},
 {'name':'wait','description':'保持安静，参数 reason'}]
ARGUMENTS={'get_memory_schema':set(),'read_context':set(),'get_member':{'user_id'},'search_history':{'query'},'list_emoticons':set(),'search_emoticons':{'query','include_unreviewed'},'view_emoticon':{'id'},'reply':{'text','emoticon','quality'},'share':{'candidate_id','text','quality'},'forward':{'message_id'},'merged_forward':{'bundle_id','message_ids','quality','route_id'},'wait':{'reason'},'update_states':{'group_state','members','images'},'finish_turn':{'group_state','members','images','action','text','candidate_id','emoticon','reason','quality','message_id','bundle_id','message_ids','route_id'}}
for _terminal in ('finish_turn','reply','share','forward','merged_forward'):
 ARGUMENTS[_terminal].update({'request_ids','request_outcome'})
READS={'get_memory_schema','update_states','read_context','get_member','search_history','list_emoticons','search_emoticons','view_emoticon'}
def decode(raw):
 value=parse_json(raw,'worker_api')
 # Bounded normalization; never let outer fields override explicit arguments.
 for _ in range(4):
  if not isinstance(value,dict):break
  if value.get('type')=='json_object' and isinstance(value.get('content'),str):
   if set(value)-{'type','content'}:raise ValueError('ambiguous_api_envelope')
   value=parse_json(value['content'],'worker_api');continue
  if 'call' not in value:
   if 'action' in value:value={'call':{'name':'finish_turn','arguments':value}}
   elif value.get('type') in ('wait','reply','share','forward','merged_forward'):
    value={'call':{'name':'finish_turn','arguments':{'action':value['type'],**{k:v for k,v in value.items() if k!='type'}}}}
  if isinstance(value.get('call'),dict) and isinstance(value['call'].get('arguments'),dict):
   args=dict(value['call']['arguments']);extra={k:v for k,v in value.items() if k!='call'}
   # Accept only fields exposed for this exact API; conflicting values remain errors.
   exposed=ARGUMENTS.get(value['call'].get('name'),set())
   if set(extra)-exposed:raise ValueError('ambiguous_api_envelope')
   if any(k in args and args[k]!=v for k,v in extra.items()):raise ValueError('conflicting_api_arguments')
   args.update(extra);value={'call':{**value['call'],'arguments':args}}
  break
 if not isinstance(value,dict) or set(value)-{'call'}:raise ValueError('worker_must_call_application_api')
 call=value.get('call')
 if not isinstance(call,dict) or set(call)-{'name','arguments'}:raise ValueError('invalid_api_call')
 if call.get('name') not in {t['name'] for t in TOOLS}:raise ValueError('api_not_exposed')
 args=call.get('arguments',{})
 if not isinstance(args,dict):raise ValueError('invalid_api_arguments')
 name=call['name'];allowed=ARGUMENTS[name]
 if set(args)-allowed:raise ValueError('argument_not_exposed')
 if name in ('reply','share') and not isinstance(args.get('text',''),str):raise ValueError('invalid_text')
 return {'name':name,'arguments':args}
def decision(call):
 name=call['name'];args=call['arguments']
 quality=args.get('quality')
 if (name in ('reply','share','merged_forward') or name=='finish_turn' and args.get('action') in ('reply','share','merged_forward')) and quality!='natural':
  result={'action':'wait','text':'','reason':'回复难接、尴尬或尚未确认自然，跳过本轮'}
  if name=='finish_turn':result['state_updates']={k:args[k] for k in ('group_state','members','images') if k in args}
  return result
 if name=='finish_turn':
  if args.get('action') not in ('wait','reply','share','forward','merged_forward'):raise ValueError('invalid_finish_action')
  if not isinstance(args.get('text',''),str):raise ValueError('invalid_text')
  return {'action':args['action'],'text':args.get('text','')[:180],'request_ids':args.get('request_ids',[]),'request_outcome':args.get('request_outcome'),'route_id':args.get('route_id'),'bundle_id':args.get('bundle_id'),'message_ids':args.get('message_ids'),'candidate_id':args.get('candidate_id'),'emoticon':args.get('emoticon'),'message_id':str(args.get('message_id','')) if args.get('message_id') else None,'reason':str(args.get('reason',''))[:200],'state_updates':{k:args[k] for k in ('group_state','members','images') if k in args}}
 if name in READS:return {'action':'read','call':call,'text':''}
 if name=='forward':
  mid=str(args.get('message_id',''))
  if not mid:raise ValueError('forward_requires_message_id')
  return {'action':'forward','text':'','message_id':mid,'emoticon':None,'reason':''}
 return {'action':name,'text':args.get('text','')[:180],'request_ids':args.get('request_ids',[]),'request_outcome':args.get('request_outcome'),'route_id':args.get('route_id'),'bundle_id':args.get('bundle_id'),'message_ids':args.get('message_ids'),'candidate_id':args.get('candidate_id'),'emoticon':args.get('emoticon'),'reason':str(args.get('reason',''))[:200]}
def read(db,cfg,target,payload,call):
 name=call['name'];args=call['arguments']
 if name=='get_memory_schema':return {k:memory_store.SCHEMA['$defs'][k] for k in ('FactInput','RelationInput','StateInput','GroupPortraitInput','PersonPortraitInput')}
 if name=='update_states':
  outcome=apply_worker_states(cfg,target,payload,args,db)
  return {**outcome,'emoticon_catalog':emoticons.catalog(cfg['context_dir'],target),'member_memories':member_context(cfg,target,payload),'group_state':group_state(cfg,target)}
 if name=='read_context':return {k:v for k,v in payload.items() if k not in ('_vision','_attached_images','tool_results')}
 if name=='list_emoticons':return emoticons.catalog(cfg['context_dir'],target)
 if name=='search_emoticons':
  query=args.get('query','');unreviewed=args.get('include_unreviewed',False)
  if not isinstance(query,str) or len(query)>200 or not isinstance(unreviewed,bool):raise ValueError('invalid_emoticon_query')
  return {'stickers':emoticons.search(cfg['context_dir'],target,query,include_unreviewed=unreviewed)}
 if name=='view_emoticon':
  identity=args.get('id')
  if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{24}',identity):raise ValueError('invalid_sticker_id')
  item=emoticons.preview(cfg['context_dir'],target,identity)
  payload['_vision']=[item]+[v for v in payload.get('_vision',[]) if v['reference']!=item['reference']][:3]
  return {'reference':item['reference'],'instruction':'下一次调用附上这张真实图片；用images状态接口描述、标记is_sticker和tags后可选择发送'}
 if name=='get_member':
  user=str(args.get('user_id',''))
  allowed={str(v['id']) for v in payload.get('member_profiles',[])}
  if user not in allowed:raise ValueError('member_outside_context')
  row=db.execute('SELECT * FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
  result=dict(row) if row else {'user_id':user,'kind':'unknown'}
  result['memory']=member_markdown(cfg,target,user)
  if memory_store.enabled(cfg['context_dir']):
   store=memory_store.MemoryStore(cfg['context_dir'])
   try:
    doc=store.load('person',user);doc['groups']={target:doc['groups'][target]} if target in doc['groups'] else {}
    result['structured_memory']=doc
   finally:store.close()
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
    # Group-specific sections from other groups are never injected into this worker.
    def keep(match):return match[0] if match[1]==str(group) else ''
    text=re.sub(r'<!-- group-state:(\d+):start -->.*?<!-- group-state:\1:end -->',keep,text,flags=re.S)
    text=re.sub(r'<!-- group-portrait:(\d+):start -->.*?<!-- group-portrait:\1:end -->',keep,text,flags=re.S)
    return re.sub(r'<!-- group-scope:(\d+):start -->.*?<!-- group-scope:\1:end -->',keep,text,flags=re.S)


def group_portrait(cfg,group):
    path=Path(cfg['context_dir'])/'groups'/safe_id(group)/'group.md'
    if not path.exists():return ''
    match=re.search(r'<!-- portrait:start -->.*?<!-- portrait:end -->',path.read_text(),flags=re.S)
    return match[0] if match else ''


def member_context(cfg,group,payload,budget=24000):
    profiles=sorted(payload.get('member_profiles',[]),key=lambda p:max((r['created'] for r in p.get('evidence_records',[])),default=0),reverse=True);result=[]
    for index,profile in enumerate(profiles):
        user=str(profile['id']);full=member_markdown(cfg,group,user)
        priority=[]
        for label in ('person-portrait','person-state','account-kind','group-scope:'+str(group),'group-portrait:'+str(group),'group-state:'+str(group)):
            match=re.search(re.escape('<!-- '+label+':start -->')+'.*?'+re.escape('<!-- '+label+':end -->'),full,flags=re.S)
            if match:priority.append(match[0]);full=full.replace(match[0],'')
        full='\n'.join(priority)+'\n'+full
        limit=min(4000,max(0,budget//max(1,len(profiles)-index)))
        text=full[:limit];budget-=len(text)
        result.append({'user_id':user,'markdown':text,'truncated':len(text)<len(full),'profile':profile['current_judgment']})
    return result


def apply_worker_states(cfg,group,payload,args,db=None):
    """Reject invalid memory without aborting a separately validated chat action."""
    try:update_states(cfg,group,payload,args,db)
    except ValueError as exc:
        code=str(exc)
        if not re.fullmatch('[a-z_]{1,100}',code):code='invalid_memory_update'
        if db is not None:
            with db:db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('state_update_error:'+str(group),code))
        return {'saved':False,'error':code,'instruction':'状态更新未通过校验，没有采纳无效内容。核对群归属、真实证据和schema；聊天决策仍可独立完成。'}
    if db is not None:
        with db:db.execute('DELETE FROM metrics WHERE key=?',('state_update_error:'+str(group),))
    return {'saved':True}


def update_states(cfg,group,payload,args,db=None):
    allowed={str(p['id']) for p in payload.get('member_profiles',[])}
    members=args.get('members',[]);state=args.get('group_state');images=args.get('images',[])
    available={v['reference']:v for v in payload.get('_attached_images',[])}
    # Optional image annotations cannot block a valid chat turn. Only annotations
    # bound to actual attached bytes are eligible for archive/library writes.
    validated_images=[];rejected=0
    if not isinstance(images,list):images=[];rejected+=1
    aliases={v.get('sticker_id'):key for key,v in available.items() if v.get('sticker_id')}
    for proposed in images[:4]:
        if not isinstance(proposed,dict):rejected+=1;continue
        image=dict(proposed);reference=image.get('reference')
        if not isinstance(reference,str):rejected+=1;continue
        if reference not in available and reference in aliases:image['reference']=aliases[reference]
        if not {'reference','description'}<=set(image) or set(image)-{'reference','description','is_sticker','tags'} or image['reference'] not in available:
            rejected+=1;continue
        if not isinstance(image['description'],str) or not image['description'].strip() or len(image['description'])>1200:
            rejected+=1;continue
        if 'is_sticker' in image and not isinstance(image['is_sticker'],bool):
            rejected+=1;continue
        tags=image.get('tags',[])
        if not isinstance(tags,list) or len(tags)>12 or any(not isinstance(tag,str) or not 1<=len(tag)<=40 for tag in tags):
            rejected+=1;continue
        validated_images.append(image)
    images=validated_images
    if rejected:payload['_rejected_image_annotations']=payload.get('_rejected_image_annotations',0)+rejected
    if not isinstance(members,list) or len(members)>60:raise ValueError('invalid_member_states')
    if state is not None:memory_store.input_patch(state,'StateInput')
    # Validate the entire request before writing any file.
    seen_users=set()
    for item in members:
        if not isinstance(item,dict) or not {'user_id','state'}<=set(item) or set(item)-{'user_id','state','account_kind','confidence','reason','evidence_keys','scope'} or str(item['user_id']) not in allowed:raise ValueError('member_outside_context')
        if str(item['user_id']) in seen_users:raise ValueError('duplicate_member_state')
        seen_users.add(str(item['user_id']))
        if item.get('scope','person') not in ('person','group'):raise ValueError('invalid_member_scope')
        memory_store.input_patch(item['state'],'StateInput')
        if 'account_kind' in item:
            if db is None or item['account_kind'] not in ('human','bot','unknown'):raise ValueError('invalid_account_kind')
            confidence=item.get('confidence');keys=item.get('evidence_keys',[])
            if isinstance(confidence,bool) or not isinstance(confidence,(float,int)) or not 0<=confidence<=1 or not isinstance(keys,list) or not 1<=len(keys)<=20:raise ValueError('invalid_account_evidence')
            for key in keys:
                if not isinstance(key,str) or not db.execute('SELECT 1 FROM speaker_events WHERE event_key=? AND group_id=? AND user_id=?',(key,str(group),str(item['user_id']))).fetchone():raise ValueError('unverified_account_evidence')
    if memory_store.enabled(cfg['context_dir']) or isinstance(state,dict) or any(isinstance(item['state'],dict) for item in members):
        store=memory_store.MemoryStore(cfg['context_dir'])
        try:
            if state is not None:store.verify_evidence(group,memory_store.input_patch(state,'StateInput'))
            for item in members:store.verify_evidence(group if item.get('scope')=='group' else None,memory_store.input_patch(item['state'],'StateInput'))
        finally:store.close()
    if db is not None:
        with db:
            for item in members:
                if 'account_kind' not in item:continue
                user=str(item['user_id']);old=db.execute('SELECT kind,source FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
                if participants.trusted_kind(user,cfg) or old and (old['kind']=='bot' or old['source']=='metadata'):continue
                confidence=item['confidence'];kind=item['account_kind'] if confidence>=.85 else 'unknown'
                participants.set_profile(db,user,kind,confidence,'应用校验证据 '+','.join(item['evidence_keys'])+'；'+str(item.get('reason',''))[:250],'app_validated_worker',time.time())
        participants.flush_profiles(db,cfg['context_dir'],members)
    for image in images:
        meta=available[image['reference']]
        if meta.get('event_key'):archive.record_visual(cfg,meta['event_key'],image['description'],meta.get('media_key',''))
        if meta.get('media_key'):emoticons.learn(cfg['context_dir'],meta['group_id'],meta.get('url',''),image['description'],meta['media_key'],original_key=meta.get('original_key'),tags=image.get('tags',[]),is_sticker=image.get('is_sticker',bool(meta.get('sticker_hint'))))
    if memory_store.enabled(cfg['context_dir']) or isinstance(state,dict) or any(isinstance(item['state'],dict) for item in members):
        store=memory_store.MemoryStore(cfg['context_dir'])
        try:store.state_updates(group,state,[(str(item['user_id']),item['state'],item.get('scope','person')) for item in members])
        finally:store.close()
        return
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
