"""One-time full re-distillation of all people portraits from archive raw messages.

Reads every message each person has sent from archive.sqlite3, asks the model in
ONE call to produce clean distilled portraits for all people, then writes them
back via MemoryStore (source=history_distillation).
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
BOT_ID = str(CFG.get('bot_id', ''))


def load_people_messages():
    """Return {user_id: [msg,...]} for every person with a people.json.

    Each msg: {event_key, nickname, text, time}.
    """
    disk = sqlite3.connect(CTX / 'archive.sqlite3')
    disk.row_factory = sqlite3.Row
    try:
        people = sorted(p.name for p in (CTX / 'people').iterdir()
                        if (p / 'people.json').exists())
        out = {}
        for uid in people:
            rows = disk.execute(
                'SELECT group_id, message_id, nickname, text, created '
                'FROM messages WHERE user_id=? ORDER BY created', (uid,)).fetchall()
            msgs = []
            for r in rows:
                text = (r['text'] or '').strip()
                if not text:
                    continue
                msgs.append({
                    'event_key': f"{r['group_id']}:{r['message_id']}",
                    'nickname': r['nickname'] or uid,
                    'text': text[:500],
                    'time': r['created'],
                })
            if msgs:
                out[uid] = msgs
        return out
    finally:
        disk.close()


def existing_summary(store, uid):
    """Compact hint of non-style/interests fields from existing portrait."""
    doc = store.load('person', uid)
    pt = doc['portrait']
    hint = {}
    for field in ('relationships', 'boundaries'):
        items = pt.get(field, [])
        if items:
            hint[field] = [
                {k: v for k, v in f.items()
                 if k in ('text', 'subject_id', 'object_id', 'relation', 'confidence')}
                for f in items[:5]
            ]
    if pt.get('summary'):
        hint['old_summary'] = pt['summary'][:300]
    return hint


PROMPT = '''你是结构化记忆蒸馏器，没有发消息能力。输入是每个人在归档里的全部原始发言。
你的任务：为每个人重新蒸馏一份精炼的人物画像，完全基于该人的原始发言，不要复述单条消息。

硬约束（违反即作废）：
1. 禁止"本轮称…""延续…风格""本次说…"等逐条发言流水句式。每条 style/interests 必须是可长期复用的稳定模式（如"短句口语接梗，偶有粗口""常聊股票仓位操作"），不是某一次说了什么。
2. style 和 interests 各 3-8 条，合并同类项，去掉重复和废话。
3. aliases 只写真实称呼/昵称（群名片、昵称），不写一次性玩梗短语。
4. boundaries 只写有明确依据的意愿/边界/偏好，没有就空数组。
5. uncertainties 只放真正未决的事实问题，禁止放"本群画像基于 N 条记录""样本不足"等元注释。没有就空数组。
6. relationships 只写有明确依据的人物关系（subject_id/object_id 为 QQ 号），没有就空数组。
7. 每条 fact 的 evidence 必须使用输入中该人消息的 event_key（格式 group_id:message_id），最多 3 条最具代表性的。
8. summary 用 1-2 句概括这个人，不超过 80 字。
9. confidence：多次出现有明确依据的给 0.85+，单次出现的给 0.5-0.7。
10. 输出严格 JSON，不要任何解释文字、不要 Markdown 代码块。

输出格式：
{"people": [{"user_id": "QQ号", "portrait": {"summary": "...", "aliases": [{"text":"...","evidence":[...],"confidence":0.x}], "style": [...], "interests": [...], "relationships": [...], "boundaries": [...], "uncertainties": [...]}}]}

每个人都要输出，即使消息很少也给一个最小画像。'''


def normalize_portrait(portrait, event_keys):
    """Normalize model output into valid PersonPortraitInput shape.

    The model often returns fact arrays as plain strings or dicts missing
    evidence/confidence. Fix them so schema validation passes.
    """
    FACT_FIELDS = ('aliases', 'style', 'interests', 'boundaries', 'uncertainties')
    fallback_keys = event_keys[:3]

    def norm_fact(item):
        if isinstance(item, str):
            return {'text': item[:4000], 'evidence': list(fallback_keys), 'confidence': 0.6}
        if isinstance(item, dict):
            text = str(item.get('text', '')).strip()
            if not text:
                return None
            evidence = item.get('evidence') or []
            if isinstance(evidence, str):
                evidence = [evidence]
            evidence = [str(e) for e in evidence if e][:30]
            if not evidence:
                evidence = list(fallback_keys)
            try:
                conf = float(item.get('confidence', 0.6))
            except (TypeError, ValueError):
                conf = 0.6
            conf = max(0.0, min(1.0, conf))
            return {'text': text[:4000], 'evidence': evidence, 'confidence': conf}
        return None

    out = {}
    if isinstance(portrait.get('summary'), str):
        out['summary'] = portrait['summary'][:4000]
    for field in FACT_FIELDS:
        items = portrait.get(field, [])
        if not isinstance(items, list):
            items = []
        normalized = []
        for it in items:
            f = norm_fact(it)
            if f:
                normalized.append(f)
        out[field] = normalized[:60]

    # relationships need subject_id, object_id, relation, evidence, confidence
    rels = portrait.get('relationships', [])
    if not isinstance(rels, list):
        rels = []
    norm_rels = []
    for it in rels:
        if not isinstance(it, dict):
            continue
        sid = str(it.get('subject_id', ''))
        oid = str(it.get('object_id', ''))
        rel = str(it.get('relation', '')).strip()
        if not (sid.isdigit() and oid.isdigit() and rel):
            continue
        evidence = it.get('evidence') or []
        if isinstance(evidence, str):
            evidence = [evidence]
        evidence = [str(e) for e in evidence if e][:30]
        if not evidence:
            evidence = list(fallback_keys)
        try:
            conf = float(it.get('confidence', 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        norm_rels.append({
            'subject_id': sid, 'object_id': oid, 'relation': rel[:1000],
            'evidence': evidence, 'confidence': max(0.0, min(1.0, conf)),
        })
    out['relationships'] = norm_rels[:60]
    return out


def main():
    store = memory_store.MemoryStore(CTX)
    try:
        people_msgs = load_people_messages()
        print(f"Loaded messages for {len(people_msgs)} people")

        # Build payload: for each person, messages + existing relationship/boundary hints
        payload_people = []
        for uid, msgs in people_msgs.items():
            hint = existing_summary(store, uid)
            payload_people.append({
                'user_id': uid,
                'message_count': len(msgs),
                'messages': msgs,
                'existing_hints': hint,
            })

        payload = {'people': payload_people}

        model = dict(social.load_model(CFG))
        model['max_completion_tokens'] = 32768
        model['temperature'] = 0.3

        cache_path = Path(CTX) / 'distill_raw_output.json'
        if cache_path.exists():
            print(f"Loading cached raw output from {cache_path}")
            raw = cache_path.read_text(encoding='utf-8')
            print(f"Cached output: {len(raw)} chars")
        else:
            # Direct HTTP call with long timeout (agent.request caps at 30s).
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
            headers = {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + social.api_key(model),
            }
            req = Request(model['base_url'].rstrip('/') + '/chat/completions',
                          data=json.dumps(body).encode(), headers=headers)

            print(f"Calling model {model['model']} (timeout=300s) ...")
            t0 = time.time()
            with urlopen(req, timeout=300) as resp:
                response = json.loads(resp.read())
            print(f"Model call done in {time.time()-t0:.1f}s")
            usage = response.get('usage', {})
            print(f"usage: prompt={usage.get('prompt_tokens')} "
                  f"completion={usage.get('completion_tokens')} "
                  f"finish={response['choices'][0].get('finish_reason')}")
            raw = response['choices'][0]['message']['content']
            cache_path.write_text(raw, encoding='utf-8')
            print(f"Raw output saved to {cache_path} ({len(raw)} chars)")

        data = json.loads(raw)
        portraits = data.get('people', [])
        print(f"Model returned {len(portraits)} portraits")

        # Build lookup of each person's message event_keys for evidence fallback.
        msg_event_keys = {uid: [m['event_key'] for m in msgs]
                          for uid, msgs in people_msgs.items()}

        now = time.time()
        written = 0
        for item in portraits:
            uid = str(item.get('user_id', ''))
            portrait = item.get('portrait', {})
            if not uid.isdigit() or not portrait:
                print(f"  skip invalid: user_id={uid}")
                continue
            try:
                portrait = normalize_portrait(portrait, msg_event_keys.get(uid, []))
                doc = store.load('person', uid)
                # Full replacement: blank the portrait first so any field the
                # model omits becomes empty instead of keeping old water.
                doc['portrait'] = memory_store.blank('PersonPortraitInput')
                memory_store.apply_patch(
                    doc['portrait'], portrait, 'PersonPortraitInput',
                    'history_distillation', now)
                # Also update coverage note to reflect full re-distillation.
                doc['coverage']['note'] = '基于归档全部原始发言重新蒸馏；旧流水式条目已清除'
                doc['coverage']['message_count'] = len(people_msgs.get(uid, []))
                store.commit([doc], 'history_distillation')
                written += 1
            except Exception as exc:
                print(f"  FAILED {uid}: {exc}")
                traceback.print_exc()

        print(f"\nWritten {written}/{len(portraits)} portraits")
    finally:
        store.close()


if __name__ == '__main__':
    main()
