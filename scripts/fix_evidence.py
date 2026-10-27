"""Fix evidence assignment for distilled portraits.

The one-time distillation attached generic fallback evidence (first 3 messages)
to every fact because the model returned arrays of plain strings. This script
re-scores each fact against the person's own messages and replaces evidence with
the most relevant ones (or empties it when nothing matches).
"""
import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
import json,re,sqlite3,time
from pathlib import Path
from app import memory_store

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / 'workflow.json').read_text())
CTX = Path(CFG['context_dir'])

STOPWORDS = set('的了是在有和就不我你他她它这那都也还就要会可以一个上下里外前后左右中大小多少好坏新旧长短高低远近快慢冷热轻重'.split())


def tokenize(text):
    # Extract 2-4 char Chinese chunks and ASCII words; drop stopwords and noise.
    tokens = set()
    for m in re.findall(r'[\u4e00-\u9fff]{2,4}|[A-Za-z0-9]{2,}', text):
        if m in STOPWORDS:
            continue
        tokens.add(m.lower())
    return tokens


def score_message(fact_text, msg_text):
    f = tokenize(fact_text)
    m = tokenize(msg_text)
    if not f or not m:
        return 0
    overlap = f & m
    # Prefer messages where a larger fraction of fact tokens appear.
    return len(overlap) / max(1, len(f))


def load_person_messages(uid):
    disk = sqlite3.connect(CTX / 'archive.sqlite3')
    try:
        rows = disk.execute(
            'SELECT group_id, message_id, text, nickname FROM messages WHERE user_id=? ORDER BY created',
            (uid,)).fetchall()
        return [(f"{r[0]}:{r[1]}", r[2] or '', r[3] or '') for r in rows]
    finally:
        disk.close()


def best_evidence(fact_text, messages, top_k=2, field='text'):
    """Find messages most relevant to fact_text.

    field='text' matches against message text; field='nickname' matches against
    the sender's nickname in that message (used for aliases).
    """
    scored = []
    for event_key, text, nickname in messages:
        target = nickname if field == 'nickname' else text
        if not target.strip():
            continue
        s = score_message(fact_text, target)
        if s > 0:
            scored.append((s, event_key))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    return [k for _, k in scored[:top_k]]


def fix_portrait(portrait, messages):
    for fact in portrait.get('aliases', []):
        ev = best_evidence(fact.get('text', ''), messages, field='nickname')
        fact['evidence'] = ev
    for field in ('style', 'interests', 'boundaries', 'uncertainties'):
        for fact in portrait.get(field, []):
            ev = best_evidence(fact.get('text', ''), messages, field='text')
            fact['evidence'] = ev
    for rel in portrait.get('relationships', []):
        ev = best_evidence(rel.get('relation', ''), messages, field='text')
        rel['evidence'] = ev


def main():
    store = memory_store.MemoryStore(CTX)
    try:
        people = sorted(p.name for p in (CTX / 'people').iterdir()
                        if (p / 'people.json').exists())
        fixed = 0
        for uid in people:
            messages = load_person_messages(uid)
            if not messages:
                continue
            doc = store.load('person', uid)
            before = json.dumps(doc['portrait'], ensure_ascii=False)
            fix_portrait(doc['portrait'], messages)
            after = json.dumps(doc['portrait'], ensure_ascii=False)
            if before != after:
                store.commit([doc], 'evidence_repair')
                fixed += 1
        print(f"Fixed evidence for {fixed}/{len(people)} people")
    finally:
        store.close()


if __name__ == '__main__':
    main()
