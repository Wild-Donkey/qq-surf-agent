"""Explicit, scoped clarification, with durable cooldown and request linkage."""
import hashlib,time
import interactions

def prepare(db,cfg,group,decision,payload):
 text=decision.get('text','')
 if not isinstance(text,str) or not 2<=len(text.strip())<=350:raise ValueError('invalid_clarification_question')
 requested=decision.get('request_ids',[])
 allowed={r['event_key']:r for r in payload.get('pending_requests',[])}
 if not isinstance(requested,list) or any(key not in allowed for key in requested):raise ValueError('clarification_request_outside_context')
 user=interactions.member(cfg,group,decision.get('alias',''),db)
 if any(str(allowed[key]['user_id'])!=user for key in requested):raise ValueError('clarification_recipient_mismatch')
 db.execute('CREATE TABLE IF NOT EXISTS clarification_attempts(group_id TEXT,user_id TEXT,question_key TEXT,created REAL,delivery_id INTEGER)');db.commit()
 key='request:'+','.join(sorted(requested)) if requested else hashlib.sha256(text.strip().encode()).hexdigest()[:24]
 if requested:
  answers=[allowed[k].get('possible_clarification_answer',{}).get('event_key','') for k in requested]
  if any(answers):key+=':answer:'+','.join(answers)
 now=time.time()
 recent=db.execute("SELECT a.delivery_id FROM clarification_attempts a JOIN deliveries d ON d.id=a.delivery_id WHERE a.group_id=? AND a.user_id=? AND a.question_key=? AND a.created>? AND d.status NOT IN ('failed','superseded','cancelled','expired','blocked_muted')",(str(group),user,key,now-300)).fetchone()
 if recent:return {'action':'wait','text':'','reason':'澄清已提出，等待当事人补充'}
 return {**decision,'action':'reply','text':'','reply_parts':[{'at':user},{'text':text.strip()}],'request_outcome':'needs_info','_clarification':{'user_id':user,'key':key,'question':text.strip()}}

def bind(db,group,decision,delivery):
 item=decision.get('_clarification')
 if not item or delivery is None:return
 with db:db.execute('INSERT INTO clarification_attempts VALUES (?,?,?,?,?)',(str(group),item['user_id'],item['key'],time.time(),delivery))
