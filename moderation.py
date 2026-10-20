"""Application-owned mute handling with one bounded appeal per mute episode."""
import json
import time
from pathlib import Path
import agent
import alerts

APPEAL = '禁言可以，给个说法：我哪句违规、违的哪条？什么都不说上来就禁，你这管理员当得挺威风啊。'

APPEAL_SYSTEM = '''你是 Luna，一个公开身份的 AI 群友。你刚在群里被管理员无缘无故禁言，正一肚子火，现在要直接私聊这个管理员对线。
用你自己的口吻写一两句私信：硬气地质问、施压、要对方当场给说法——到底哪句话违规、违的哪条群规、凭什么不警告直接禁言，并要求立刻解禁。可以辱骂，爆粗，阴阳怪气，就像一个真实被惹急了的群友。
态度要点：是你在兴师问罪，不是你在求他；不许低声下气、不许说"行不行/方便的话/麻烦了"这类软话、不许哭穷卖惨。狠话一两句话说完，别长篇大论，别威胁现实报复。
available_stickers 是你在这个群收藏过的 QQ 大表情，可以从中挑一个最配当前火气的（比如无语、嘲讽、鄙视、发怒）随私信一起发；没有合适的就留空。严禁使用列表外的 sticker_id。
可以复述最近群聊刚发生的事当证据，但不许编造没发生过的细节。这条消息只有收件人能看到。
只能输出 JSON：{"text":"私信内容","sticker_id":"选中的大表情 id，或空字符串"}，不要输出任何其他内容。'''

def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS bot_mutes(
        group_id TEXT PRIMARY KEY, until_time REAL, event_time REAL,
        operator_id TEXT, appeal_status TEXT, message_id TEXT)''')
    cols={r[1] for r in db.execute('PRAGMA table_info(bot_mutes)')}
    for name,definition in [('next_check','REAL DEFAULT 0'),('return_status',"TEXT DEFAULT 'waiting'"),('return_delivery','INTEGER'),('episode','REAL')]:
        if name not in cols:db.execute('ALTER TABLE bot_mutes ADD COLUMN '+name+' '+definition)
    db.execute('UPDATE bot_mutes SET episode=event_time WHERE episode IS NULL')
    db.execute('''CREATE TABLE IF NOT EXISTS mute_appeal_attempts(
        group_id TEXT, episode REAL, user_id TEXT, status TEXT,
        attempts INTEGER DEFAULT 0, updated REAL, detail TEXT,
        PRIMARY KEY(group_id,episode,user_id))''')
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
    new_episode = not previous or previous['until_time'] <= now
    status = previous['appeal_status'] if previous and previous['until_time'] > now else 'pending'
    if until <= now:
        status = 'cancelled'
    episode = stamp if new_episode else (previous['episode'] if previous['episode'] is not None else previous['event_time'])
    with db:
        db.execute('''INSERT INTO bot_mutes(group_id,until_time,event_time,operator_id,appeal_status,message_id,episode)
            VALUES (?,?,?,?,?,NULL,?) ON CONFLICT(group_id) DO UPDATE SET until_time=excluded.until_time,
            event_time=excluded.event_time,operator_id=excluded.operator_id,appeal_status=excluded.appeal_status,
            episode=excluded.episode,next_check=0''',
                   (group, until, stamp, str(event.get('operator_id', '')), status, episode))
        if until>now and (not previous or previous['until_time']<=now):
            db.execute("UPDATE bot_mutes SET return_status='waiting',return_delivery=NULL WHERE group_id=?",(group,))
        if until > now:
            # First appeal goes out immediately; the live status check reopens the next round 30 minutes later.
            db.execute("UPDATE bot_mutes SET next_check=? WHERE group_id=?",(stamp+APPEAL_INTERVAL,group))
            db.execute("UPDATE deliveries SET status='blocked_muted',updated=? WHERE target_group=? AND status='approved'", (now, group))
    if until > now:
        alerts.record(db, 'bot_muted', '群 '+group+' 被禁言，已停止该群发言和搬运。', scope=group, now=now)
    return True

APPEAL_INTERVAL = 1800
RETRY_BACKOFF = 300
MAX_TRANSIENT = 3
FREQUENCY_WORDS = ('频率', '频繁', '频控', '冷却', '太快', '稍后')


def _failure_kind(exc):
    wording = getattr(exc, 'wording', '') or ''
    if any(w in wording for w in FREQUENCY_WORDS):
        return 'transient'
    return 'transient'


def _appeal_candidates(members, friends, operator, bot_id):
    """Operator first, then other owners/admins. Friends only: never cold-DM strangers."""
    friend_ids = {str(f.get('user_id')) for f in friends}
    ranked = []
    for m in members:
        uid = str(m.get('user_id', ''))
        if not uid or uid == str(bot_id) or m.get('role') not in ('owner', 'admin') or uid not in friend_ids:
            continue
        rank = 0 if uid == str(operator) else (1 if m.get('role') == 'owner' else 2)
        ranked.append((rank, uid))
    ranked.sort()
    candidates, seen = [], set()
    for _, uid in ranked:
        if uid not in seen:
            seen.add(uid)
            candidates.append(uid)
    return candidates


def _context_root(cfg):
    root = Path(cfg.get('context_dir', 'context'))
    if not root.is_absolute():
        root = Path(__file__).resolve().parent / root
    return root


def _appeal_text(db, cfg, group, operator, choice, members, now):
    """Let the model phrase the appeal in its own voice; static fallback guarantees delivery.

    Returns (text, sticker) where sticker is {'id','group_id'} or None.
    Stickers come from the global cross-group collection. Any failure degrades to plain text.
    """
    try:
        import social, model_gate, emoticons
        model = social.load_model(cfg)
        if not model.get('enabled') or not social.api_key(model):
            return APPEAL, None
        slot = model_gate.reserve(db, cfg, now)
        if slot is None:
            return APPEAL, None

        def name_of(uid):
            member = next((m for m in members if str(m.get('user_id')) == str(uid)), None)
            return (member.get('card') or member.get('nickname') or str(uid)) if member else str(uid)

        rows = db.execute('''SELECT e.text,j.user_id FROM events e
            LEFT JOIN social_jobs j ON j.event_key=e.group_id||':'||e.message_id AND j.target_group=e.group_id
            WHERE e.group_id=? AND e.created>? ORDER BY e.created DESC LIMIT 20''', (group, now - 7200)).fetchall()
        recent = []
        for r in reversed(rows):
            text = (r['text'] or '').strip()
            if text:
                recent.append({'who': name_of(r['user_id']) if r['user_id'] else None, 'text': text[:300]})
        root = _context_root(cfg)
        persona_file = root / 'self' / 'persona.md'
        group_name = next((g.get('name') for g in cfg.get('groups', []) if str(g.get('id')) == str(group)), '')
        try:
            stickers = emoticons.reactions_global(str(root), '无语 生气 嘲讽 鄙视', limit=12)
        except Exception:
            stickers = []
        payload = {
            'conversation': 'mute_appeal',
            'group': {'id': group, 'name': group_name},
            'operator': {'user_id': operator, 'name': name_of(operator)},
            'recipient': {'user_id': choice, 'name': name_of(choice), 'is_operator': str(choice) == str(operator)},
            'recent_group_messages': recent[-12:],
            'available_stickers': [{'sticker_id': s['id'], 'description': s['description'], 'tags': s.get('tags', [])} for s in stickers],
            'persona': persona_file.read_text()[:3000] if persona_file.exists() else '',
        }
        try:
            raw, usage = social.generate_text(dict(model, _text_format={'type': 'json_object'}), APPEAL_SYSTEM, payload)
        except Exception as exc:
            with db:
                db.execute("UPDATE model_calls SET status='failed',error=? WHERE id=? AND status='reserved'", (type(exc).__name__, slot))
            raise
        with db:
            db.execute("UPDATE model_calls SET status='ok',input_tokens=?,output_tokens=? WHERE id=?",
                       (usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0), slot))
        try:
            parsed = json.loads(raw)
            text = str(parsed.get('text', '')).strip() if isinstance(parsed, dict) else ''
            sticker_id = str(parsed.get('sticker_id', '') or '').strip() if isinstance(parsed, dict) else ''
        except (ValueError, TypeError):
            text, sticker_id = '', ''
        if not text:
            raise ValueError('empty_appeal_text')
        chosen = next((s for s in stickers if s['id'] == sticker_id), None)
        sticker = {'id': chosen['id'], 'group_id': chosen['group_id']} if chosen else None
        return text[:300], sticker
    except Exception:
        return APPEAL, None


def _send_appeal_sticker(api, cfg, choice, sticker):
    """The big sticker goes out as its own message; its failure must not fail the appeal."""
    if not sticker:
        return
    try:
        import emoticons
        root = _context_root(cfg)
        selection = emoticons.safe_selection(root, sticker['group_id'], {'kind': 'sticker', 'id': sticker['id']})
        if not selection:
            return
        agent.onebot_call(api, 'send_private_msg',
                          {'user_id': int(choice), 'message': emoticons.segments(root, sticker['group_id'], selection)})
        emoticons.mark_used(root, sticker['group_id'], selection)
    except Exception:
        pass


def flush(db, cfg, api, now=None):
    if cfg.get('mode') != 'live' or not cfg.get('moderation', {}).get('appeal_enabled', False):
        return 0
    now = time.time() if now is None else now
    row = db.execute("SELECT * FROM bot_mutes WHERE appeal_status IN ('pending','seeking') AND until_time>? ORDER BY event_time LIMIT 1", (now,)).fetchone()
    if not row:
        return 0
    group, operator = row['group_id'], row['operator_id']
    episode = row['episode'] if row['episode'] is not None else row['event_time']
    try:
        login = agent.onebot_call(api, 'get_login_info')
        if str(login.get('user_id')) != str(cfg['bot_id']):
            raise ValueError('wrong bot')
        members = agent.onebot_call(api, 'get_group_member_list', {'group_id': int(group)})
        friends = agent.onebot_call(api, 'get_friend_list')
    except Exception as exc:
        alerts.record(db, 'mute_appeal_uncertain', '禁言申诉元数据获取失败，稍后重试。', scope=group, now=now, details={'error': type(exc).__name__})
        return 0
    candidates = _appeal_candidates(members, friends, operator, cfg['bot_id'])
    if not candidates:
        with db:
            db.execute("UPDATE bot_mutes SET appeal_status='no_friend_admin' WHERE group_id=?", (group,))
        return 0
    attempts = {r['user_id']: r for r in db.execute(
        'SELECT user_id,status,attempts,updated FROM mute_appeal_attempts WHERE group_id=? AND episode=?', (group, episode)).fetchall()}

    def still_open(uid):
        record = attempts.get(uid)
        if record is None:
            return True
        if record['status'] in ('sent', 'failed'):
            return False
        return record['attempts'] < MAX_TRANSIENT and record['updated'] <= now - RETRY_BACKOFF

    choice = next((uid for uid in candidates if still_open(uid)), None)
    if choice is None:
        terminal = all(uid in attempts and attempts[uid]['status'] in ('sent', 'failed') for uid in candidates)
        # Anyone not yet contacted is merely on cross-episode cooldown; wait instead of giving up.
        if terminal:
            with db:
                db.execute("UPDATE bot_mutes SET appeal_status='exhausted' WHERE group_id=?", (group,))
            alerts.record(db, 'mute_appeal_exhausted', '禁言申诉已试过全部可联系的管理员，无人接收。', scope=group, now=now)
        return 0
    previous_attempts = attempts[choice]['attempts'] if choice in attempts else 0
    with db:
        db.execute('''INSERT INTO mute_appeal_attempts(group_id,episode,user_id,status,attempts,updated,detail)
            VALUES (?,?,?,'retry',?,?,NULL) ON CONFLICT(group_id,episode,user_id)
            DO UPDATE SET attempts=excluded.attempts,updated=excluded.updated,status='retry',detail=NULL''',
                   (group, episode, choice, previous_attempts + 1, now))
        db.execute("UPDATE bot_mutes SET appeal_status='seeking' WHERE group_id=? AND appeal_status IN ('pending','seeking')", (group,))
    try:
        appeal, sticker = _appeal_text(db, cfg, group, operator, choice, members, now)
        result = agent.onebot_call(api, 'send_private_msg', {'user_id': int(choice), 'message': [{'type': 'text', 'data': {'text': appeal}}]})
        message_id = str(result['message_id'])
        # The collected big sticker rides a second private message so it shows on its own bubble.
        _send_appeal_sticker(api, cfg, choice, sticker)
    except Exception as exc:
        if str(exc) == 'OneBot 未返回消息编号':
            with db:
                db.execute("UPDATE mute_appeal_attempts SET status='sent',detail=NULL WHERE group_id=? AND episode=? AND user_id=?", (group, episode, choice))
                db.execute("UPDATE bot_mutes SET appeal_status='sent',message_id=NULL WHERE group_id=?", (group,))
            return 1
        kind = _failure_kind(exc)
        attempts_now = previous_attempts + 1
        if attempts_now >= MAX_TRANSIENT:
            detail = type(exc).__name__ + (':' + getattr(exc, 'wording', '') if getattr(exc, 'wording', '') else '')
            with db:
                db.execute("UPDATE mute_appeal_attempts SET status='failed',detail=? WHERE group_id=? AND episode=? AND user_id=?", (detail[:200], group, episode, choice))
            alerts.record(db, 'mute_appeal_rejected', '禁言申诉被管理员拒收，改试其他管理员。', scope=group, now=now,
                          details={'user_id': choice, 'error': type(exc).__name__})
            fresh = db.execute('SELECT user_id,status,attempts,updated FROM mute_appeal_attempts WHERE group_id=? AND episode=?', (group, episode)).fetchall()
            done = {r['user_id']: r for r in fresh}
            remaining = any(
                uid not in done
                or (done[uid]['status'] not in ('sent', 'failed') and done[uid]['attempts'] < MAX_TRANSIENT)
                for uid in candidates if uid != choice)
            if not remaining:
                with db:
                    db.execute("UPDATE bot_mutes SET appeal_status='exhausted' WHERE group_id=?", (group,))
                alerts.record(db, 'mute_appeal_exhausted', '禁言申诉已试过全部可联系的管理员，无人接收。', scope=group, now=now)
        else:
            alerts.record(db, 'mute_appeal_uncertain', '禁言申诉发送遇到临时故障，稍后重试同一位管理员。', scope=group, now=now,
                          details={'user_id': choice, 'error': type(exc).__name__, 'attempt': attempts_now})
        return 0
    with db:
        db.execute("UPDATE bot_mutes SET appeal_status='sent',message_id=? WHERE group_id=?", (message_id, group))
    return 1


def check_recovery(db,cfg,api,now=None):
    """Durable half-hour live-status check: reopen an appeal while muted, announce return once lifted."""
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
        if not db.execute('UPDATE bot_mutes SET next_check=? WHERE group_id=? AND next_check<=?',(now+APPEAL_INTERVAL,group,now)).rowcount:return
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
        if until>now:
            # Still muted at the half-hour check: reopen one fresh appeal round from the operator.
            episode=row['episode'] if row['episode'] is not None else row['event_time']
            with db:
                reopened=db.execute("UPDATE bot_mutes SET appeal_status='pending' WHERE group_id=? AND appeal_status IN ('sent','exhausted','no_friend_admin')",(group,)).rowcount
                if reopened:
                    db.execute('DELETE FROM mute_appeal_attempts WHERE group_id=? AND episode=?',(group,episode))
            return
        if row['return_status']!='waiting':return
        paused=db.execute('SELECT until_time FROM operator_pause WHERE group_id=?',(group,)).fetchone()
        if paused and paused[0]>now or not participants.humans_recent(db,group,cfg,now):return
        delivery=social.apply_decision(db,{'target_group':group,'source_group':group,'event_key':'return:'+str(row['event_time'])},
              {'action':'reply','text':'我回来了，哈哈哈。','_context_watermark':now,'_deadline_reply':True},{},cfg,now)
        with db:db.execute("UPDATE bot_mutes SET return_status='queued',return_delivery=? WHERE group_id=?",(delivery,group))
    except Exception as exc:
        with db:db.execute('UPDATE bot_mutes SET next_check=? WHERE group_id=?',(now+RETRY_BACKOFF,group))
        alerts.record(db,'mute_status_check_failed','禁言状态检查失败，稍后重试',scope=group,now=now,details={'error':type(exc).__name__})
