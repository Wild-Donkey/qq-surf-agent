"""Tentative account judgments and a hard five-minute human-presence gate."""
import json
import time
from memory import atomic_write,safe_id


def ensure_schema(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS speaker_profiles(
      user_id TEXT PRIMARY KEY,kind TEXT,confidence REAL,reason TEXT,source TEXT,updated REAL);
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
    db.execute('INSERT OR REPLACE INTO speaker_profiles VALUES (?,?,?,?,?,?)',(user,kind,confidence,reason[:120],source,now))


def observe(db,event,cfg,text,now):
    user=str(event.get('user_id',''));group=str(event['group_id'])
    if not user.isdigit():return
    stamp=min(now,float(event.get('time',now)))
    db.execute('INSERT OR IGNORE INTO speaker_events VALUES (?,?,?,?,?)',
               (group+':'+str(event['message_id']),group,user,stamp,text[:600]))
    known=trusted_kind(user,cfg)
    if known:
        set_profile(db,user,known,1,'配置确认','config',now);return
    sender=event.get('sender',{})
    if sender.get('is_bot') is True or sender.get('bot') is True:
        set_profile(db,user,'bot',1,'接口标记为机器人','metadata',now);return
    # Repeated high-rate output is suspicious, not evidence of human activity.
    rows=db.execute('SELECT text FROM speaker_events WHERE user_id=? AND created>? ORDER BY created DESC LIMIT 30',(user,now-60)).fetchall()
    if len(rows)>=20 and max(sum(r['text']==s['text'] for s in rows) for r in rows)>=len(rows)*0.8:
        set_profile(db,user,'unknown',0.9,'高频重复输出，疑似自动账号','behavior',now)
    elif not db.execute('SELECT 1 FROM speaker_profiles WHERE user_id=?',(user,)).fetchone():
        set_profile(db,user,'unknown',0,'尚未判断','initial',now)


def evidence(db,batch):
    result=[]
    for user in dict.fromkeys(str(row['user_id']) for row in batch):
        profile=db.execute('SELECT kind,confidence,reason FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
        samples=db.execute('SELECT text FROM speaker_events WHERE user_id=? ORDER BY created DESC LIMIT 5',(user,)).fetchall()
        result.append({'id':user,'current_judgment':dict(profile) if profile else {'kind':'unknown'},'recent_samples':[r['text'][:180] for r in reversed(samples)]})
    return result


def apply_judgments(db,decision,batch,cfg,now):
    allowed={str(row['user_id']) for row in batch}
    judgments=decision.get('speakers',[])
    if not isinstance(judgments,list):return
    threshold=cfg.get('human_presence',{}).get('confidence_threshold',0.85)
    for item in judgments[:30]:
        if not isinstance(item,dict):continue
        user=str(item.get('id',''));kind=item.get('kind');confidence=item.get('confidence')
        if user not in allowed or trusted_kind(user,cfg):continue
        old=db.execute('SELECT source,updated FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
        if old and (old['source']=='metadata' or old['source']=='behavior' and now-old['updated']<600):continue
        if kind not in ('human','bot','unknown') or isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not 0<=confidence<=1:continue
        if confidence<threshold:kind='unknown'
        set_profile(db,user,kind,confidence,str(item.get('reason','')),'model',now)


def humans_recent(db,group,cfg,now=None):
    settings=cfg.get('human_presence',{})
    if not settings.get('enabled',False):return True
    now=time.time() if now is None else now
    # Join against current verdicts: reclassifying an account immediately invalidates its old "human" activity.
    row=db.execute('''SELECT max(e.created) FROM speaker_events e JOIN speaker_profiles p ON e.user_id=p.user_id
      WHERE e.group_id=? AND p.kind='human' AND p.confidence>=? AND e.created<=?''',
      (str(group),settings.get('confidence_threshold',0.85),now)).fetchone()
    return bool(row and row[0] is not None and now-row[0]<settings.get('timeout_seconds',300))


def prune(db,now):
    db.execute('DELETE FROM speaker_events WHERE created<?',(now-48*3600,))


def flush_profiles(db,root,batch):
    from pathlib import Path
    for user in {str(row['user_id']) for row in batch}:
        row=db.execute('SELECT * FROM speaker_profiles WHERE user_id=?',(user,)).fetchone()
        if not row:continue
        path=Path(root)/'people'/safe_id(user)/'people.md'
        start='<!-- account-kind:start -->';end='<!-- account-kind:end -->'
        block=start+'\n## 账号类型判断\n\n'+row['kind']+'；置信度 '+str(row['confidence'])+'；依据：'+row['reason'].replace('<!--','&lt;!--')+'。这只是当前判断，可被后续证据修正。\n'+end
        old=path.read_text() if path.exists() else '# 成员记忆\n'
        if start in old and end in old:
            before,rest=old.split(start,1);_,after=rest.split(end,1);new=before+block+after
        else:new=old.rstrip()+'\n\n'+block+'\n'
        if new!=old:atomic_write(path,new)
