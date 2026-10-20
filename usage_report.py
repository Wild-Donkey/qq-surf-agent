"""Persistent service usage windows; reports contain counters, never chat or credentials."""
from datetime import datetime, timezone, timedelta
import time
import alerts

TZ=timezone(timedelta(hours=8))


def totals(db,start,end):
    row=db.execute('''SELECT count(*) AS calls,
      coalesce(sum(status='ok'),0) AS ok,
      coalesce(sum(status='failed'),0) AS failed,
      coalesce(sum(input_tokens),0) AS inputs,
      coalesce(sum(output_tokens),0) AS outputs
      FROM model_calls WHERE created>=? AND created<?''',(start,end)).fetchone()
    return dict(row)


def summary(db,start,end):
    current=totals(db,start,end);daily=totals(db,end-86400,end)
    stamp=lambda t:datetime.fromtimestamp(t,TZ).strftime('%m-%d %H:%M')
    pending=current['calls']-current['ok']-current['failed']
    return (f"北京时间 {stamp(start)}—{stamp(end)}\n"
            f"本期调用 {current['calls']} 次：成功 {current['ok']}，失败 {current['failed']}，待确认 {pending}\n"
            f"输入 {current['inputs']:,} / 输出 {current['outputs']:,} token\n"
            f"近24小时调用 {daily['calls']} 次，已记录 {daily['inputs']+daily['outputs']:,} token\n"
            '仅 bot 用量；失败请求的 token 可能未计入。')


def tick(db,cfg,now=None):
    now=time.time() if now is None else now
    settings=cfg.get('usage_report',{})
    if not settings.get('enabled') or not str(cfg.get('alerts',{}).get('owner_qq','')).isdigit():return False
    interval=max(60,float(settings.get('interval_seconds',10800)))
    with db:
        row=db.execute("SELECT value FROM metrics WHERE key='usage_report_window_start'").fetchone()
        if not row:
            db.execute('INSERT INTO metrics VALUES (?,?)',('usage_report_window_start',str(now)))
            return False
        start=float(row[0])
        if now-start<interval:return False
        # Restart after downtime emits one aggregate report, not a burst of missed reports.
        text=summary(db,start,now)
        db.execute("UPDATE alerts SET status='superseded' WHERE code='usage_report' AND status='pending'")
        # record() commits with the same connection transaction; scheduling cursor and outbox are atomic.
        db.execute('INSERT OR REPLACE INTO metrics VALUES (?,?)',('usage_report_window_start',str(now)))
        alerts.record(db,'usage_report',text,scope=str(now),now=now)
    return True
