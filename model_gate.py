"""Shared atomic model start budget for application services."""
import time
def reserve(db,cfg,now=None,background=False):
 now=time.time() if now is None else now
 interaction=cfg.get('interaction',{});gap=max(1/max(.001,float(interaction.get('max_qps',.2))),float(interaction.get('min_model_interval_seconds',0)))
 db.execute('BEGIN IMMEDIATE')
 try:
  if background and db.execute("SELECT 1 FROM social_jobs WHERE status='processing' LIMIT 1").fetchone():db.rollback();return None
  last=db.execute('SELECT max(created) FROM model_calls').fetchone()[0]
  if last and now-last<gap:db.rollback();return None
  row=db.execute("INSERT INTO model_calls(created,status) VALUES (?,'reserved')",(now,)).lastrowid;db.commit();return row
 except Exception:db.rollback();raise
