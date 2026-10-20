"""Application-owned mute handling with one bounded appeal per mute episode."""
import time
import agent
import alerts

APPEAL = '我刚才哪句违规了？你指出来，我按群规改。上来就禁言，连个理由都没有，这谁服气？'

def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS bot_mutes(
        group_id TEXT PRIMARY KEY, until_time REAL, event_time REAL,
        operator_id TEXT, appeal_status TEXT, message_id TEXT)''')
    db.execute("CREATE TABLE IF NOT EXISTS mute_appeal_contacts(user_id TEXT PRIMARY KEY,last_attempt REAL)")
    cols={r[1] for r in db.execute('PRAGMA table_info(bot_mutes)')}
    for name,definition in [('next_check','REAL DEFAULT 0'),('return_status',"TEXT DEFAULT 'waiting'"),('return_delivery','INTEGER')]:
        if name not in cols:db.execute('ALTER TABLE bot_mutes ADD COLUMN '+name+' '+definition)
    db.commit()

def is_muted(db, group, now=None):
    now = time.time() if now is None else now
    row = db.execute('SELECT until_time FROM bot_mutes WHERE group_id=?', (str(group),)).fetchone()
    return bool(row and row[0] > now)

def ingest(db, event, cfg, now):
    if event.get('post_type') != 'notice' or event.get('notice_type') != 'group_ban':
        return False
    group = str(event.get('group_id', ''))
    if group not in {str(g['id']) for g in cfg['groups'] if g.get('enabled', True)}:
        return True
    if str(event.get('user_id')) != str(cfg['bot_id']):
        return True
    try:
        stamp = float(event.get('time', now))
        duration = max(0, int(event.get('duration', 0)))
    except (TypeError, ValueError):
        return True
    previous = db.execute('SELECT * FROM bot_mutes WHERE group_id=?', (group,)).fetchone()
    if previous and stamp <= previous['event_time']:
        return True
    until = 0 if event.get('sub_type') == 'lift_ban' else stamp + duration
    # Extending an existing mute never generates another DM.
    status = previous['appeal_status'] if previous and previous['until_time'] > now else 'pending'
    if until <= now:
        status = 'cancelled'
    with db:
        db.execute('''INSERT INTO bot_mutes(group_id,until_time,event_time,operator_id,appeal_status,message_id)
            VALUES (?,?,?,?,?,NULL) ON CONFLICT(group_id) DO UPDATE SET until_time=excluded.until_time,
            event_time=excluded.event_time,operator_id=excluded.operator_id,appeal_status=excluded.appeal_status,
            next_check=0''',
                   (group, until, stamp, str(event.get('operator_id', '')), status))
        if until>now and (not previous or previous['until_time']<=now):
            db.execute("UPDATE bot_mutes SET return_status='waiting',return_delivery=NULL WHERE group_id=?",(group,))
        if until > now:
            db.execute("UPDATE deliveries SET status='blocked_muted',updated=? WHERE target_group=? AND status='approved'", (now, group))
    if until > now:
        alerts.record(db, 'bot_muted', '群 '+group+' 被禁言，已停止该群发言和搬运。', scope=group, now=now)
    return True

def flush(db, cfg, api, now=None):
    if cfg.get('mode') != 'live' or not cfg.get('moderation', {}).get('appeal_enabled', False):
        return 0
    now = time.time() if now is None else now
    row = db.execute("SELECT * FROM bot_mutes WHERE appeal_status='pending' AND until_time>? ORDER BY event_time LIMIT 1", (now,)).fetchone()
    if not row:
        return 0
    group, recipient = row['group_id'], row['operator_id']
    # Claim before any external operation: uncertain results are never retried.
    with db:
        if not db.execute("UPDATE bot_mutes SET appeal_status='uncertain' WHERE group_id=? AND appeal_status='pending'", (group,)).rowcount:
            return 0
    with db:
        claimed=db.execute('''INSERT INTO mute_appeal_contacts VALUES (?,?)
            ON CONFLICT(user_id) DO UPDATE SET last_attempt=excluded.last_attempt
            WHERE mute_appeal_contacts.last_attempt<=?''',(recipient,now,now-86400)).rowcount
        if not claimed:
            db.execute("UPDATE bot_mutes SET appeal_status='cooldown' WHERE group_id=?",(group,))
            return 0
    try:
        login = agent.onebot_call(api, 'get_login_info')
        if str(login.get('user_id')) != str(cfg['bot_id']):
            raise ValueError('wrong bot')
        members = agent.onebot_call(api, 'get_group_member_list', {'group_id': int(group)})
        friends = agent.onebot_call(api, 'get_friend_list')
        allowed = recipient != str(cfg['bot_id']) and any(str(m.get('user_id')) == recipient and m.get('role') in ('owner', 'admin') for m in members)
        allowed = allowed and any(str(f.get('user_id')) == recipient for f in friends)
        if not allowed:
            with db: db.execute("UPDATE bot_mutes SET appeal_status='no_friend_admin' WHERE group_id=?", (group,))
            return 0
        result = agent.onebot_call(api, 'send_private_msg', {'user_id': int(recipient), 'message': [{'type':'text','data':{'text':APPEAL}}]})
        with db: db.execute("UPDATE bot_mutes SET appeal_status='sent',message_id=? WHERE group_id=? AND event_time=?", (str(result['message_id']),group,row['event_time']))
        return 1
    except Exception as exc:
        alerts.record(db, 'mute_appeal_uncertain', '禁言申诉结果待核实，不会重发。', scope=group, now=now, details={'error':type(exc).__name__})
        return 0


def check_recovery(db,cfg,api,now=None):
    """Durable five-minute timer, with live QQ metadata as evidence."""
    import participants,social
    now=time.time() if now is None else now
    if cfg.get('mode')!='live':return
    for row in db.execute("SELECT group_id,return_delivery FROM bot_mutes WHERE return_status='queued'").fetchall():
        delivery=db.execute('SELECT status FROM deliveries WHERE id=?',(row['return_delivery'],)).fetchone()
        if delivery:
            if delivery[0] in ('sent','already_seen','uncertain','cancelled'):
                with db:db.execute("UPDATE bot_mutes SET return_status='done' WHERE group_id=?",(row['group_id'],))
            elif delivery[0] in ('superseded','expired','blocked_muted'):
                with db:db.execute("UPDATE bot_mutes SET return_status='waiting',return_delivery=NULL WHERE group_id=?",(row['group_id'],))
    row=db.execute("SELECT * FROM bot_mutes WHERE (until_time>0 OR return_status='waiting') AND next_check<=? ORDER BY next_check LIMIT 1",(now,)).fetchone()
    if not row:return
    group=row['group_id']
    if group not in {str(g['id']) for g in cfg.get('groups',[]) if g.get('enabled',True)}:return
    with db:
        if not db.execute('UPDATE bot_mutes SET next_check=? WHERE group_id=? AND next_check<=?',(now+300,group,now)).rowcount:return
    try:
        login=agent.onebot_call(api,'get_login_info')
        if str(login.get('user_id'))!=str(cfg['bot_id']):raise ValueError('wrong_bot')
        members=agent.onebot_call(api,'get_group_member_list',{'group_id':int(group)})
        me=next(m for m in members if str(m.get('user_id'))==str(cfg['bot_id']))
        if 'shut_up_timestamp' not in me:raise ValueError('missing_mute_status')
        until=float(me['shut_up_timestamp'])
        with db:
            unchanged=db.execute('UPDATE bot_mutes SET until_time=? WHERE group_id=? AND event_time=?',(until if until>now else 0,group,row['event_time'])).rowcount
        if not unchanged:return
        if until>now or row['return_status']!='waiting':return
        paused=db.execute('SELECT until_time FROM operator_pause WHERE group_id=?',(group,)).fetchone()
        if paused and paused[0]>now or not participants.humans_recent(db,group,cfg,now):return
        delivery=social.apply_decision(db,{'target_group':group,'source_group':group,'event_key':'return:'+str(row['event_time'])},
              {'action':'reply','text':'我回来了，按群规继续聊。','_context_watermark':now,'_deadline_reply':True},{},cfg,now)
        with db:db.execute("UPDATE bot_mutes SET return_status='queued',return_delivery=? WHERE group_id=?",(delivery,group))
    except Exception as exc:
        alerts.record(db,'mute_status_check_failed','禁言恢复检查失败，继续等待核实。',scope=group,now=now,details={'error':type(exc).__name__})
