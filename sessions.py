"""One bounded group session, one short-lived reaction agent per message-triggered job."""
from dataclasses import dataclass,field
import hashlib
import time


@dataclass
class GroupSession:
    group_id:str
    last_activity:float
    turns:list=field(default_factory=list)
    cached_memory:dict=field(default_factory=dict)

    def recall(self,memory,user=None):
        key=str(user or '')
        if key not in self.cached_memory and len(self.cached_memory)>=8:self.cached_memory.pop(next(iter(self.cached_memory)))
        self.cached_memory[key]=memory.retrieve(self.group_id,user,12000)
        return self.cached_memory[key]


@dataclass
class ReactionAgent:
    """A stateless child task. It cannot select arbitrary tools, paths, or recipients."""
    task_id:str
    target_group:str
    session:GroupSession

    @classmethod
    def create(cls,job,session):
        ident=hashlib.sha256((job['event_key']+':'+job['target_group']).encode()).hexdigest()[:16]
        return cls(ident,job['target_group'],session)


class SessionPool:
    def __init__(self):self.groups={}

    def sync(self,db,cfg,now=None):
        now=time.time() if now is None else now
        idle=cfg.get('interaction',{}).get('idle_flush_seconds',300)
        rows=db.execute("SELECT group_id,max(created) AS latest FROM events WHERE created>? AND message_id NOT LIKE 'bot:%' GROUP BY group_id",(now-idle,)).fetchall()
        for row in rows:
            group=row['group_id']
            session=self.groups.setdefault(group,GroupSession(group,row['latest']))
            session.last_activity=max(session.last_activity,row['latest'])
            self._refresh(db,session,cfg)

    @staticmethod
    def _refresh(db,session,cfg):
        rows=db.execute('''SELECT e.text,e.created,e.message_id,j.user_id FROM events e LEFT JOIN social_jobs j
            ON j.event_key=e.group_id||':'||e.message_id AND j.target_group=e.group_id
            WHERE e.group_id=? ORDER BY e.created DESC LIMIT ?''',
                        (session.group_id,cfg.get('context_messages',100))).fetchall()
        budget=cfg.get('context_char_budget',24000);turns=[]
        for row in rows:
            speaker='自己' if row['message_id'].startswith('bot:') else str(row['user_id'] or '未缓存发言者')
            text=('['+speaker+'] '+row['text'])[:min(budget,1200)]
            if not text:continue
            turns.append({'text':text,'time':row['created']});budget-=len(text)
            if budget<=0:break
        session.turns=list(reversed(turns))

    def task(self,db,cfg,job,now=None):
        now=time.time() if now is None else now
        session=self.groups.setdefault(job['target_group'],GroupSession(job['target_group'],now))
        self._refresh(db,session,cfg)
        return ReactionAgent.create(job,session)

    def flush_active(self,memory):
        for session in self.groups.values():
            memory.flush_session(session.group_id,session.turns)
            # Checkpointing is a copy; active context remains until idle expiry.

    def expire(self,memory,cfg,now=None):
        now=time.time() if now is None else now
        timeout=cfg.get('interaction',{}).get('idle_flush_seconds',300)
        expired=[key for key,session in self.groups.items() if now-session.last_activity>=timeout]
        for key in expired:
            session=self.groups.pop(key)
            memory.flush_session(key,session.turns)
        if expired:memory.flush(cfg['groups'])
        return expired
