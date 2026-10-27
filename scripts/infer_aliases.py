"""Model-based alias inference for all people.

For each person, collect:
- their own messages (for their nickname)
- messages from OTHERS that @mention or reply to them (for how they're addressed)

Then call the model ONCE to extract aliases for everyone. People whose alias
cannot be determined get an empty aliases list.
"""
import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
import json,sqlite3,time,traceback
from pathlib import Path
from urllib.request import Request, urlopen
from app import memory_store
from app import social

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / 'workflow.json').read_text())
CTX = Path(CFG['context_dir'])


def load_person_context(uid, db):
    """Return own messages + addressing messages from others."""
    # Own messages
    own = []
    for r in db.execute(
        'SELECT group_id, message_id, nickname, text FROM messages WHERE user_id=? ORDER BY created',
        (uid,)):
        text = (r[3] or '').strip()
        own.append({
            'event_key': f"{r[0]}:{r[1]}",
            'nickname': r[2] or '',
            'text': text[:300],
        })

    # Their message IDs + groups
    their_msg_ids = set()
    their_groups = set()
    for r in db.execute('SELECT message_id, group_id FROM messages WHERE user_id=?', (uid,)):
        their_msg_ids.add(str(r[0]))
        their_groups.add(r[1])

    # Messages from others that mention or reply to this person
    addressing = []
    if their_groups:
        placeholders = ','.join('?' * len(their_groups))
        for r in db.execute(
            f'SELECT group_id, message_id, user_id, nickname, text FROM messages '
            f'WHERE group_id IN ({placeholders}) AND mentions LIKE ?',
            tuple(their_groups) + (f'%{uid}%',)):
            if str(r[2]) == uid:
                continue
            addressing.append({
                'event_key': f"{r[0]}:{r[1]}",
                'from_nickname': r[3] or '',
                'text': (r[4] or '').strip()[:300],
                'type': 'mention',
            })
        for gid in their_groups:
            for r in db.execute(
                'SELECT group_id, message_id, user_id, nickname, text, reply_to '
                'FROM messages WHERE group_id=?', (gid,)):
                if str(r[2]) == uid:
                    continue
                if r[5] and str(r[5]) in their_msg_ids:
                    addressing.append({
                        'event_key': f"{r[0]}:{r[1]}",
                        'from_nickname': r[3] or '',
                        'text': (r[4] or '').strip()[:300],
                        'type': 'reply',
                    })
    return own, addressing


PROMPT = '''你是结构化记忆的称呼提取器，没有发消息能力。
输入是每个人自己的发言记录，以及别人@他或回复他时的发言。
你的任务：为每个人提取"称呼"（aliases）——即别人怎么叫他、以及他自己的昵称。

规则：
1. 称呼必须有明确依据：要么是该人自己用过的昵称，要么是别人在@他/回复他时对他的称呼。
2. 如果无法确定称呼（没人叫他、或他自己也没用固定昵称），aliases 返回空数组 []，不要瞎编。
3. 每条 alias 的 evidence 必须使用输入消息的 event_key（格式 group_id:message_id），指向那条能证明该称呼的消息。最多 2 条。
4. 不要把一次性玩梗、临时称呼写进去，只写相对稳定的称呼。
5. 不要把长句、元注释、描述性文字当称呼。称呼通常是 1-6 个字的简短名字。
6. 输出严格 JSON，不要任何解释文字、不要 Markdown 代码块。

输出格式：
{"people": [{"user_id": "QQ号", "aliases": [{"text": "称呼", "evidence": ["event_key1"], "confidence": 0.x}]}]}

每个人都要输出，无法确定就返回空 aliases。'''


def main():
    db = sqlite3.connect(CTX / 'archive.sqlite3')
    store = memory_store.MemoryStore(CTX)
    try:
        people = sorted(p.name for p in (CTX / 'people').iterdir()
                        if (p / 'people.json').exists())
        print(f"Processing {len(people)} people")

        payload_people = []
        for uid in people:
            own, addressing = load_person_context(uid, db)
            if not own:
                continue
            payload_people.append({
                'user_id': uid,
                'own_messages': own,
                'addressing_messages': addressing,
            })

        payload = {'people': payload_people}

        cache_path = Path(CTX) / 'alias_raw_output.json'
        if cache_path.exists():
            print(f"Loading cached output from {cache_path}")
            raw = cache_path.read_text(encoding='utf-8')
        else:
            model = dict(social.load_model(CFG))
            model['max_completion_tokens'] = 32768
            model['temperature'] = 0.2
            user_content = json.dumps(payload, ensure_ascii=False)
            body = {
                'model': model['model'],
                'messages': [
                    {'role': 'system', 'content': PROMPT},
                    {'role': 'user', 'content': user_content},
                ],
                'response_format': {'type': 'json_object'},
                'max_tokens': model['max_completion_tokens'],
                'temperature': model['temperature'],
            }
            if model.get('reasoning_effort'):
                body['reasoning_effort'] = model['reasoning_effort']
            headers = {'Content-Type': 'application/json',
                       'Authorization': 'Bearer ' + social.api_key(model)}
            req = Request(model['base_url'].rstrip('/') + '/chat/completions',
                          data=json.dumps(body).encode(), headers=headers)
            print(f"Calling model {model['model']} ...")
            t0 = time.time()
            with urlopen(req, timeout=300) as resp:
                response = json.loads(resp.read())
            print(f"Done in {time.time()-t0:.1f}s")
            usage = response.get('usage', {})
            print(f"usage: prompt={usage.get('prompt_tokens')} "
                  f"completion={usage.get('completion_tokens')}")
            raw = response['choices'][0]['message']['content']
            cache_path.write_text(raw, encoding='utf-8')

        data = json.loads(raw)
        results = data.get('people', [])
        print(f"Model returned {len(results)} results")

        now = time.time()
        written = 0
        for item in results:
            uid = str(item.get('user_id', ''))
            aliases = item.get('aliases', [])
            if not uid.isdigit():
                continue
            try:
                doc = store.load('person', uid)
                # Normalize: ensure each alias has text, evidence (list), confidence
                norm = []
                for a in aliases:
                    if isinstance(a, str):
                        norm.append({'text': a[:100], 'evidence': [], 'confidence': 0.6})
                    elif isinstance(a, dict) and a.get('text'):
                        ev = a.get('evidence') or []
                        if isinstance(ev, str):
                            ev = [ev]
                        ev = [str(e) for e in ev if e][:5]
                        try:
                            conf = float(a.get('confidence', 0.6))
                        except (TypeError, ValueError):
                            conf = 0.6
                        norm.append({'text': str(a['text'])[:100], 'evidence': ev,
                                     'confidence': max(0.0, min(1.0, conf))})
                # Replace aliases entirely
                doc['portrait']['aliases'] = []
                if norm:
                    memory_store.apply_patch(
                        doc['portrait'], {'aliases': norm},
                        'PersonPortraitInput', 'history_distillation', now)
                store.commit([doc], 'history_distillation')
                written += 1
            except Exception as exc:
                print(f"  FAILED {uid}: {exc}")
                traceback.print_exc()

        print(f"\nWritten {written}/{len(results)} people")
    finally:
        store.close()
        db.close()


if __name__ == '__main__':
    main()
