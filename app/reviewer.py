"""事后慢路审阅：快路回复发出后，由审阅者判断是否有问题，
有问题则撤回原消息并用慢路结果重发。

设计原则：
- 审阅靠模型推理，不靠关键词规则表；审阅 prompt 只描述"什么样算有问题"，
  让模型结合上下文自己判断。
- 撤回窗口受 QQ 两分钟限制；慢任务若在窗口内完成则直接重发，否则 reply_to 原消息。
- 审阅本身也消耗模型预算，默认关闭，由 workflow.json 的 reviewer.enabled 控制。
"""
import json
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RECALL_WINDOW_SECONDS=120


def ensure_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS reviews(delivery_id INTEGER PRIMARY KEY, group_id TEXT,
      message_id TEXT, status TEXT, needs_revision INTEGER, review_reasoning TEXT,
      issues TEXT, revision_direction TEXT, error TEXT, created REAL, updated REAL);
    CREATE INDEX IF NOT EXISTS reviews_status ON reviews(status,updated);
    ''')
    db.commit()


def get_supervisor_hint(db,cfg,group_id):
    """聚合近期审阅发现，生成给 worker 的一两句提示。

    只看近 24 小时、该群、needs_revision=1 的审阅记录。若不足 1 条则不提示，
    避免无根据的干扰。提示语由真实 issue 驱动，不写死规则。
    """
    rcfg=cfg.get('reviewer',{})
    if not rcfg.get('enabled'):return ''
    window=time.time()-86400
    rows=db.execute('''
        SELECT issues, revision_direction FROM reviews
        WHERE group_id=? AND needs_revision=1 AND updated>?
        ORDER BY updated DESC LIMIT 10
    ''',(str(group_id),window)).fetchall()
    if not rows:return ''
    # 统计 issue 频次，取最突出的 1-2 类
    freq={}
    directions=[]
    for r in rows:
        try:issues=json.loads(r['issues']) if r['issues'] else []
        except (ValueError,TypeError):issues=[]
        for iss in issues:
            key=str(iss)[:60]
            freq[key]=freq.get(key,0)+1
        if r['revision_direction']:
            directions.append(str(r['revision_direction'])[:120])
    if not freq:return ''
    top=sorted(freq.items(),key=lambda x:-x[1])[:2]
    issue_text='、'.join(k for k,_ in top)
    hint='近期审阅发现你在本群的回复存在以下问题：'+issue_text+'。'
    if directions:
        hint+='改进方向：'+directions[0]+'。'
    hint+='本轮回复请特别注意避免同类问题。'
    return hint[:300]


def tick(db,cfg,sessions,api_cfg):
    """审阅主入口：扫描近两分钟内已发送的 reply，逐条审阅。"""
    rcfg=cfg.get('reviewer',{})
    if not rcfg.get('enabled'):return 0
    now=time.time()
    window=now-RECALL_WINDOW_SECONDS
    limit=int(rcfg.get('max_reviews_per_tick',5))
    rows=db.execute('''
        SELECT d.id AS delivery_id,d.target_group AS group_id,d.message_id,d.updated,
               c.text AS original_text,c.kind
        FROM deliveries d
        JOIN candidates c ON c.id=d.candidate_id
        LEFT JOIN reviews r ON r.delivery_id=d.id
        WHERE d.status='sent' AND d.message_id IS NOT NULL
          AND c.kind='reply' AND d.updated>? AND r.delivery_id IS NULL
        ORDER BY d.updated DESC LIMIT ?
    ''',(window,limit)).fetchall()
    if not rows:return 0
    revised=0
    from app import social,service_log
    model=social.load_model(cfg)
    if not model.get('enabled'):return 0
    for row in rows:
        try:
            if _review_one(db,cfg,api_cfg,model,dict(row)):
                revised+=1
        except Exception as exc:
            service_log.event('surf-bot','review failed delivery='+str(row['delivery_id'])+' err='+type(exc).__name__+':'+str(exc)[:120])
            with db:
                db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (row['delivery_id'],row['group_id'],row['message_id'],'failed',0,
                            None,None,None,type(exc).__name__,now,now))
    return revised


def _review_context(db,cfg,target,message_id,original_text):
    """构造审阅上下文：群近期消息 + 原回复。"""
    # 取原回复前后近期消息（含原回复本身），供审阅者判断语境
    rows=db.execute('''
        SELECT message_id,text,created FROM events
        WHERE group_id=? AND created>?
        ORDER BY created DESC LIMIT 15
    ''',(str(target),time.time()-1800)).fetchall()
    timeline=[]
    for r in rows:
        speaker='bot' if str(r['message_id']).startswith('bot:') else 'member'
        timeline.append({'speaker':speaker,'text':r['text'][:500],'time':r['created']})
    timeline.reverse()
    return {
        'group_id':str(target),
        'original_reply':original_text[:1000],
        'timeline':timeline,
    }


def _review_prompt(context):
    return '''你是群聊回复质量审阅者。下面是群聊近期上下文和 bot 刚发出的一条回复。

请仔细推理：这条回复是否存在问题？可能的问题包括（不限于）：自相矛盾、无中生有（编造上下文中没有的信息/人名/经历）、该调用工具却没调用（如群友问群里有哪些人、某梗什么意思，bot 凭猜测回答而没查）、答非所问、事实错误。

不要用关键词机械判断，要结合上下文推理。如果认为回复有问题且可以改进，输出：
{"needs_revision": true, "reasoning": "你的判断依据", "issues": ["具体问题"], "revision_direction": "改进方向"}

如果回复没问题，输出：
{"needs_revision": false, "reasoning": "为什么没问题"}

只输出一个 JSON 对象。

=== 群聊近期消息 ===
''' + '\n'.join(f"[{m['speaker']}] {m['text']}" for m in context['timeline']) + f'''

=== bot 刚发出的回复 ===
{context['original_reply']}'''


def _review_one(db,cfg,api_cfg,model,delivery):
    from app import social,service_log
    now=time.time()
    context=_review_context(db,cfg,delivery['group_id'],delivery['message_id'],delivery['original_text'])
    prompt=_review_prompt(context)
    # 审阅是判断类任务，给一点轻量思维（light），让模型对照上下文推理而非机械判词
    review_model=dict(model,_text_format={'type':'json_object'})
    rcfg=cfg.get('reviewer',{})
    review_effort=rcfg.get('review_reasoning_effort','low')
    if review_model.get('provider')!='chatgpt':review_model['reasoning_effort']=review_effort
    try:
        raw,_usage=social.generate_text(review_model,prompt,{})
    except Exception as exc:
        raise RuntimeError('review_model_call_failed:'+type(exc).__name__) from exc
    try:
        verdict=json.loads(raw)
    except ValueError:
        raise RuntimeError('review_invalid_json')
    if not isinstance(verdict,dict):
        raise RuntimeError('review_not_object')
    needs=bool(verdict.get('needs_revision'))
    reasoning=str(verdict.get('reasoning') or '')[:500]
    issues=verdict.get('issues') if isinstance(verdict.get('issues'),list) else []
    direction=str(verdict.get('revision_direction') or '')[:300]
    with db:
        db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (delivery['delivery_id'],delivery['group_id'],delivery['message_id'],
                    'reviewed',1 if needs else 0,reasoning,
                    json.dumps(issues,ensure_ascii=False) if issues else None,
                    direction or None,None,now,now))
    if not needs:
        service_log.event('surf-bot','review ok delivery='+str(delivery['delivery_id'])+' group='+str(delivery['group_id']))
        return False
    # 需要改进：尝试撤回 + 慢路重生
    service_log.event('surf-bot','review needs revision delivery='+str(delivery['delivery_id'])+' group='+str(delivery['group_id'])+' issues='+json.dumps(issues,ensure_ascii=False)[:200])
    return _revise_and_resend(db,cfg,api_cfg,model,delivery,context,issues,direction)


def _revise_and_resend(db,cfg,api_cfg,model,delivery,context,issues,direction):
    """撤回原消息，用慢路重新生成并发送。撤回失败则 reply_to 原消息。"""
    from app import qq,service_log,social
    now=time.time()
    message_id=str(delivery['message_id']).removeprefix('bot:')
    # 先尝试撤回（同步，QQ 撤回是单次 API 调用很快）
    recalled=False
    try:
        qq.recall(db,cfg,api_cfg,delivery['group_id'],message_id)
        recalled=True
    except Exception as exc:
        service_log.event('surf-bot','recall before resend failed delivery='+str(delivery['delivery_id'])+' err='+str(exc)[:120])
    # 慢路重生：用更大输出额度，conditional 注入原回复问题
    revise_prompt=_revise_prompt(context,issues,direction,reply_to=(not recalled),original_message_id=message_id)
    slow_model=dict(model,_text_format={'type':'json_object'})
    if slow_model.get('provider')!='chatgpt':
        slow_model['reasoning_effort']='none'
        slow_model['max_completion_tokens']=min(6144,max(model.get('max_completion_tokens',4096)*2,3000))
    try:
        raw,_usage=social.generate_text(slow_model,revise_prompt,{})
    except Exception as exc:
        with db:
            db.execute("UPDATE reviews SET status='failed',error=?,updated=? WHERE delivery_id=?",
                       ('slow_revise_failed:'+type(exc).__name__,now,delivery['delivery_id']))
        return False
    try:
        revised=json.loads(raw)
    except ValueError:
        with db:
            db.execute("UPDATE reviews SET status='failed',error='slow_revise_invalid_json',updated=? WHERE delivery_id=?",
                       (now,delivery['delivery_id']))
        return False
    text=str(revised.get('text') or revised.get('reply') or '').strip()
    if not text:
        with db:
            db.execute("UPDATE reviews SET status='failed',error='slow_revise_empty',updated=? WHERE delivery_id=?",
                       (now,delivery['delivery_id']))
        return False
    # 发送慢路结果
    from app import agent
    parts=[{'type':'text','data':{'text':text[:1200]}}]
    if not recalled:
        # 撤回失败：用 reply_to 引用原消息发送
        parts=[{'type':'reply','data':{'id':int(message_id)}}]+parts
    try:
        result=agent.onebot_call(api_cfg,'send_group_msg',{'group_id':int(delivery['group_id']),'message':parts})
        new_mid=str(result.get('message_id',''))
        with db:
            db.execute("UPDATE reviews SET status='done',updated=? WHERE delivery_id=?",
                       (now,delivery['delivery_id']))
        service_log.event('surf-bot','revised resend delivery='+str(delivery['delivery_id'])+' group='+str(delivery['group_id'])+' recalled='+str(recalled)+' new_message_id='+new_mid)
        return True
    except Exception as exc:
        with db:
            db.execute("UPDATE reviews SET status='failed',error=?,updated=? WHERE delivery_id=?",
                       ('resend_failed:'+type(exc).__name__,now,delivery['delivery_id']))
        service_log.event('surf-bot','revised resend failed delivery='+str(delivery['delivery_id'])+' err='+str(exc)[:120])
        return False


def _revise_prompt(context,issues,direction,reply_to,original_message_id):
    issues_text='\n'.join(f'- {i}' for i in issues) if issues else '（审阅者未列出具体问题）'
    mode='撤回失败，需要用引用回复的方式补发改进版（reply_to 原消息）' if reply_to else '原消息已撤回，直接发送改进版'
    return f'''你在群里刚发了一条回复，审阅发现它有以下问题：
{issues_text}

改进方向：{direction or '请基于上下文重新生成更准确的回复'}

当前模式：{mode}。

请基于下面的群聊上下文，直接输出改进后的回复内容。要求：
- 如果回答需要群成员、历史原话、记忆事实、网页信息等外部信息，先说明你需要查什么（应用会自动调用工具）；如果上下文已有足够信息，直接给答案。
- 只输出回复正文，不要解释你在改什么、不要输出 JSON、不要带"改进版"等标签。
- 回复要自然，像群友随口说的。

=== 群聊近期消息 ===
''' + '\n'.join(f"[{m['speaker']}] {m['text']}" for m in context['timeline']) + f'''

=== 你之前发的（有问题的）回复 ===
{context['original_reply']}'''
