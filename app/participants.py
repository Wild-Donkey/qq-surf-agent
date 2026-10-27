"""Tentative account judgments and a hard five-minute human-presence gate."""
import json
import re
import time
from app.memory import atomic_write,safe_id


def ensure_schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS speaker_profiles(
      user_id TEXT PRIMARY KEY,kind TEXT,confidence REAL,reason TEXT,source TEXT,updated REAL);
      CREATE TABLE IF NOT EXISTS account_relations(event_key TEXT,target_id TEXT,controller_id TEXT,created REAL,quote TEXT,PRIMARY KEY(event_key,target_id));
      CREATE TABLE IF NOT EXISTS speaker_judgments(
      id INTEGER PRIMARY KEY,user_id TEXT,kind TEXT,confidence REAL,reason TEXT,source TEXT,created REAL,evidence TEXT);
      CREATE TABLE IF NOT EXISTS speaker_events(
      event_key TEXT PRIMARY KEY,group_id TEXT,user_id TEXT,created REAL,text TEXT);
      CREATE INDEX IF NOT EXISTS speaker_activity ON speaker_events(group_id,created);''')
    db.commit()


def trusted_kind(user,cfg):
    settings=cfg.get('human_presence',{})
    if user==str(cfg['bot_id']) or user in {str(x) for x in settings.get('known_bots',[])}:return 'bot'
    if user in {str(x) for x in settings.get('known_humans',[])}:return 'human'
    return None


def set_profile(db,user,kind,confidence,reason,source,now):
    previous=db.execute('SELECT kind,confidence,reason,source FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
    db.execute('INSERT OR REPLACE INTO speaker_profiles VALUES (?,?,?,?,?,?)',(user,kind,confidence,reason[:500],source,now))
    if not previous or tuple(previous)!=(kind,confidence,reason[:500],source):
        samples=[dict(r) for r in db.execute('SELECT event_key,text FROM speaker_events WHERE user_id=? ORDER BY created DESC LIMIT 20',(user,))]
        db.execute('INSERT INTO speaker_judgments(user_id,kind,confidence,reason,source,created,evidence) VALUES (?,?,?,?,?,?,?)',(user,kind,confidence,reason[:500],source,now,json.dumps(samples,ensure_ascii=False)))


def observe(db,event,cfg,text,now):
    user=str(event.get('user_id',''));group=str(event['group_id'])
    if not user.isdigit():return
    stamp=min(now,float(event.get('time',now)))
    db.execute('INSERT OR IGNORE INTO speaker_events VALUES (?,?,?,?,?)',
               (group+':'+str(event['message_id']),group,user,stamp,text[:600]))
    infer_managed_account(db,event,cfg,text,stamp,now)
    known=trusted_kind(user,cfg)
    if known:
        set_profile(db,user,known,1,'配置确认','config',now);return
    sender=event.get('sender',{})
    if sender.get('is_bot') is True or sender.get('bot') is True:
        set_profile(db,user,'bot',1,'接口标记为机器人','metadata',now);return
    managed=db.execute("SELECT r.target_id FROM account_relations r JOIN speaker_profiles p ON p.user_id=r.target_id WHERE r.controller_id=? AND p.kind='bot' LIMIT 1",(user,)).fetchone()
    samples=db.execute('SELECT count(*) FROM speaker_events WHERE user_id=?',(user,)).fetchone()[0]
    old_kind=db.execute('SELECT kind FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
    if managed and samples>=3 and (not old_kind or old_kind['kind']!='bot'):
        set_profile(db,user,'human',.9,'应用推断：多轮参与群聊，并定向管理已识别bot '+managed[0]+' 的上下文；属于运营者角色，可被更强证据修正','app_rules',now)
    existing=db.execute('SELECT kind FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
    if existing and existing['kind']=='bot':return
    # Repeated high-rate output is suspicious, not evidence of human activity.
    rows=db.execute('SELECT text FROM speaker_events WHERE user_id=? AND created>? ORDER BY created DESC LIMIT 30',(user,now-60)).fetchall()
    if len(rows)>=20 and max(sum(r['text']==s['text'] for s in rows) for r in rows)>=len(rows)*0.8:
        set_profile(db,user,'unknown',0.9,'高频重复输出，疑似自动账号','behavior',now)
    elif not db.execute('SELECT 1 FROM speaker_profiles WHERE user_id=?',(user,)).fetchone():
        set_profile(db,user,'unknown',0,'尚未判断','initial',now)


def evidence(db,batch,include_samples=True):
    result=[]
    for user in dict.fromkeys(str(row['user_id']) for row in batch):
        profile=db.execute('SELECT kind,confidence,reason,source FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
        samples=db.execute('SELECT event_key,group_id,text,created FROM speaker_events WHERE user_id=? ORDER BY created DESC LIMIT 20',(user,)).fetchall() if include_samples else []
        result.append({'id':user,'current_judgment':dict(profile) if profile else {'kind':'unknown'},'recent_samples':[r['text'][:180] for r in reversed(samples)],'evidence_records':[dict(r) for r in reversed(samples)]})
    return result


def apply_judgments(db,decision,batch,cfg,now,observed_users=()):
    allowed={str(row['user_id']) for row in batch}|{str(user) for user in observed_users}
    judgments=decision.get('speakers',[])
    if not isinstance(judgments,list):return
    threshold=cfg.get('human_presence',{}).get('confidence_threshold',0.85)
    for item in judgments[:30]:
        if not isinstance(item,dict):continue
        user=str(item.get('id',''));kind=item.get('kind');confidence=item.get('confidence')
        if user not in allowed or trusted_kind(user,cfg):continue
        old=db.execute('SELECT source,updated,kind FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
        if old and (old['source']=='metadata' or old['source']=='behavior' and now-old['updated']<600):continue
        if kind not in ('human','bot','unknown') or isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not 0<=confidence<=1:continue
        if confidence<threshold:kind='unknown'
        # Fluent speech is not evidence that an established bot became human.
        if old and old['kind']=='bot' and (kind=='unknown' or kind=='human' and confidence<0.98):continue
        set_profile(db,user,kind,confidence,str(item.get('reason','')),'model',now)


def humans_recent(db,group,cfg,now=None):
    settings=cfg.get('human_presence',{})
    if not settings.get('enabled',False):return True
    now=time.time() if now is None else now
    # Only known bots are excluded; unknown speakers count as present.
    row=db.execute('''SELECT max(e.created) FROM speaker_events e LEFT JOIN speaker_profiles p ON e.user_id=p.user_id
      WHERE e.group_id=? AND (p.kind IS NULL OR p.kind!='bot') AND e.created<=?''',
      (str(group),now)).fetchone()
    return bool(row and row[0] is not None and now-row[0]<settings.get('timeout_seconds',300))


def prune(db,now):
    db.execute('DELETE FROM speaker_events WHERE created<?',(now-48*3600,))


def flush_profiles(db,root,batch):
    from pathlib import Path
    for user in {str(row['user_id']) for row in batch}:
        row=db.execute('SELECT * FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
        if not row:continue
        if (Path(root)/'structured_memory.sqlite3').exists():
            from app.memory_store import MemoryStore
            store=MemoryStore(root)
            try:store.account(user,dict(row))
            finally:store.close()
            continue
        path=Path(root)/'people'/safe_id(user)/'people.md'
        start='<!-- account-kind:start -->';end='<!-- account-kind:end -->'
        block=start+'\n## 账号类型判断\n\n来源 '+row['source']+'；'+row['kind']+'；置信度 '+str(row['confidence'])+'；依据：'+row['reason'].replace('<!--','&lt;!--')+'。这只是当前判断，可被后续证据修正。\n'+end
        old=path.read_text() if path.exists() else '# 成员记忆\n'
        if start in old and end in old:
            before,rest=old.split(start,1);_,after=rest.split(end,1);new=before+block+after
        else:new=old.rstrip()+'\n\n'+block+'\n'
        if new!=old:atomic_write(path,new)


def sync_config(db,cfg,now=None):
    now=time.time() if now is None else now
    settings=cfg.get('human_presence',{})
    users={str(cfg['bot_id'])}|{str(x) for x in settings.get('known_bots',[])}|{str(x) for x in settings.get('known_humans',[])}
    with db:
        for user in users:set_profile(db,user,trusted_kind(user,cfg),1,'配置确认','config',now)
    flush_profiles(db,cfg['context_dir'],[{'user_id':user} for user in users])


def infer_managed_account(db,event,cfg,text,stamp,now):
    """Application policy: directed maintenance + target acknowledgement, never nickname."""
    user=str(event['user_id']);group=str(event['group_id'])
    parts=event.get('message') if isinstance(event.get('message'),list) else []
    targets={str(p.get('data',{}).get('qq','')) for p in parts if isinstance(p,dict) and p.get('type')=='at'}
    if re.search(r'清理.{0,6}上下文|重置.{0,6}上下文|修改.{0,6}提示词',text):
        for target in targets:
            if target.isdigit() and target!=user:
                db.execute('INSERT OR IGNORE INTO account_relations VALUES (?,?,?,?,?)',(group+':'+str(event['message_id']),target,user,stamp,text[:600]))
    # A directed maintenance request alone is a claim; the target's own response corroborates it.
    if re.search(r'别清|不要清|别重置|token.{0,10}(免费|计费|额度)|我是.{0,8}(AI|机器人|bot)',text,re.I):
        relation=db.execute('SELECT * FROM account_relations WHERE target_id=? AND created BETWEEN ? AND ? ORDER BY created DESC LIMIT 1',(user,stamp-180,stamp)).fetchone()
        if relation and not trusted_kind(user,cfg):
            set_profile(db,user,'bot',.97,'应用证据：'+relation['controller_id']+'定向要求“'+relation['quote'][:100]+'”；该账号回应“'+text[:100]+'”','app_rules',now)


def reconcile_archive(db,cfg):
    """Replay facts into application identity state; does not create worker jobs or send messages."""
    from pathlib import Path
    import sqlite3
    marker='identity_application_policy_v1'
    if db.execute('SELECT 1 FROM metrics WHERE key=?',(marker,)).fetchone():return
    with db:
        for row in db.execute("SELECT user_id FROM speaker_profiles WHERE source='model'").fetchall():
            set_profile(db,row['user_id'],'unknown',0,'旧聊天worker判断已作废，等待应用证据复核','app_migration',time.time())
    path=Path(cfg['context_dir'])/'archive.sqlite3'
    if path.exists():
        saved=sqlite3.connect(path);saved.row_factory=sqlite3.Row
        try:
            for row in saved.execute('SELECT raw_json FROM messages ORDER BY created,id'):
                event=json.loads(row['raw_json'])
                if not str(event.get('user_id','')).isdigit():continue
                parts=event.get('message',[])
                text=''.join(str(p.get('data',{}).get('text','')) for p in parts if isinstance(p,dict) and p.get('type')=='text')
                with db:observe(db,event,cfg,text,time.time())
        finally:saved.close()
    sync_config(db,cfg)
    users=[{'user_id':row[0]} for row in db.execute('SELECT user_id FROM speaker_profiles')]
    flush_profiles(db,cfg['context_dir'],users)
    with db:db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',(marker,str(time.time())))
