"""Durable, deduplicated owner notifications. No secrets or raw chat in alerts."""
import hashlib
import json
import sqlite3
import time
import urllib.error
from app import agent


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS alerts (
      id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, code TEXT, summary TEXT,
      count INTEGER, first_seen REAL,last_seen REAL,status TEXT,notify_after REAL,
      message_id TEXT)''')
    if 'details' not in {r[1] for r in db.execute('PRAGMA table_info(alerts)')}:
        db.execute('ALTER TABLE alerts ADD COLUMN details TEXT')
    db.commit()


def record(db,code,summary,scope='service',now=None,details=None):
    now=time.time() if now is None else now
    fp=hashlib.sha256((str(code)+':'+str(scope)).encode()).hexdigest()
    summary=str(summary)[:600 if code=='usage_report' else 240]
    # Callers supply fixed summaries and error codes, never exception bodies or credentials.
    with db:
        row=db.execute('SELECT id,status,notify_after FROM alerts WHERE fingerprint=?',(fp,)).fetchone()
        if row:
            status='pending' if row['status']=='sent' and row['notify_after']<=now else row['status']
            db.execute('UPDATE alerts SET count=count+1,last_seen=?,status=?,summary=? WHERE id=?',(now,status,summary,row['id']))
        else:db.execute('INSERT INTO alerts(fingerprint,code,summary,count,first_seen,last_seen,status,notify_after) VALUES (?,?,?,?,?,?,?,?)',
                        (fp,str(code)[:80],summary,1,now,now,'pending',now))
        if details is not None:
            db.execute('UPDATE alerts SET details=? WHERE fingerprint=?',(json.dumps(details,ensure_ascii=False)[:4000],fp))


def flush(db,cfg,api_cfg,now=None):
    now=time.time() if now is None else now
    owner=str(cfg.get('alerts',{}).get('owner_qq',''))
    if not owner.isdigit():return 0
    row=db.execute("SELECT * FROM alerts WHERE status='pending' AND notify_after<=? ORDER BY first_seen LIMIT 1",(now,)).fetchone()
    if not row:return 0
    with db:
        if not db.execute("UPDATE alerts SET status='uncertain' WHERE id=? AND status='pending'",(row['id'],)).rowcount:return 0
    try:
        login=agent.onebot_call(api_cfg,'get_login_info')
        if str(login.get('user_id'))!=str(cfg['bot_id']):raise ValueError('Wrong bot account')
        text=('bot 用量报告\n'+row['summary']) if row['code']=='usage_report' else (''+row['summary']+'\n'+row['code'])
        result=agent.onebot_call(api_cfg,'send_private_msg',{'user_id':int(owner),'message':[{'type':'text','data':{'text':text}}]})
        with db:db.execute("UPDATE alerts SET status='sent',message_id=?,notify_after=? WHERE id=?",(str(result['message_id']),now+cfg.get('alerts',{}).get('cooldown_seconds',1800),row['id']))
        return 1
    except Exception as exc:
        # Connection refusal is known not to have sent a message; retry when connectivity returns.
        if isinstance(exc,urllib.error.URLError) and isinstance(exc.reason,ConnectionRefusedError):
            with db:db.execute("UPDATE alerts SET status='pending',notify_after=? WHERE id=?",(now+60,row['id']))
        elif isinstance(exc,(ValueError,urllib.error.HTTPError)):
            with db:db.execute("UPDATE alerts SET status='pending',notify_after=? WHERE id=?",(now+300,row['id']))
        # Timeout remains uncertain and is not blindly re-sent.
        return 0
