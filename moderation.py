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
        db.execute('INSERT OR REPLACE INTO bot_mutes VALUES (?,?,?,?,?,NULL)',
                   (group, until, stamp, str(event.get('operator_id', '')), status))
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
