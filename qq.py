#!/usr/bin/env python3
"""QQ operator CLI: talk as the bot and temporarily take the floor."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import agent
import alerts
import workflow
ROOT=Path(__file__).resolve().parent


def load():
    cfg=json.loads((ROOT/'workflow.json').read_text());api=json.loads((ROOT/'config.json').read_text())
    path=Path(cfg['database'])
    if not path.is_absolute():path=ROOT/path
    path.parent.mkdir(parents=True,exist_ok=True)
    return cfg,api,workflow.connect(path)


def resolve_group(query,cfg):
    matches=[g for g in cfg['groups'] if g.get('enabled',True) and (str(g['id'])==str(query) or str(query).casefold() in g['name'].casefold())]
    if len(matches)!=1:raise ValueError('群名不唯一或不存在；使用 groups 查看，或直接填群号')
    return matches[0]


def pause(db,group,minutes):
    if not 0<=minutes<=1440:raise ValueError('暂停时间应为0到1440分钟')
    with db:db.execute('INSERT OR REPLACE INTO operator_pause VALUES (?,?)',(str(group),time.time()+minutes*60))


MANUAL_MARK='【人工接管】'

def manual_text(text):
    text=text.strip()
    return text if text.startswith(MANUAL_MARK) else MANUAL_MARK+' '+text

def send(db,cfg,api,target,text,private=False,dry_run=False):
    text=text.strip()
    if not text or len(text)>2000:raise ValueError('消息应为1到2000字')
    text=manual_text(text)
    if not private and not workflow.group_is_active(db,target,cfg):
        raise ValueError('这个群近14天没有可靠的其他成员发言记录，按你的规则保持安静')
    if private and str(target)!=str(cfg.get('alerts',{}).get('owner_qq')):
        raise ValueError('此CLI的私信收件人固定为配置的主人账号')
    if dry_run:
        print('[预览，不发送] '+str(target)+'\n'+text);return None
    login=agent.onebot_call(api,'get_login_info')
    if str(login.get('user_id'))!=str(cfg['bot_id']):raise ValueError('登录账号与bot不一致')
    fp=hashlib.sha256(text.encode()).hexdigest();now=time.time()
    with db:
        ident=db.execute('INSERT INTO operator_log(target,kind,fingerprint,status,created) VALUES (?,?,?,?,?)',
                         (str(target),'private' if private else 'group',fp,'uncertain',now)).lastrowid

    action='send_private_msg' if private else 'send_group_msg'
    payload={'user_id' if private else 'group_id':int(target),'message':[{'type':'text','data':{'text':text}}]}
    try:
        result=agent.onebot_call(api,action,payload)
        with db:
            db.execute("UPDATE operator_log SET status='sent',message_id=? WHERE id=?",(str(result['message_id']),ident))
            if not private:
                db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',(str(target),'bot:'+str(result['message_id']),fp,'[自己·人工接管] '+text,'[]',now))
                db.execute('INSERT OR IGNORE INTO seen VALUES (?,?,?)',(str(target),workflow.fingerprint(text,[]),now))
        print('已发送，消息编号：'+str(result['message_id']))
        if not private:
            import operator_control
            try:operator_control.owner_message(db,cfg,target,result['message_id'],text)
            except Exception:
                import service_log
                service_log.event('surf-bot','owner notification pending group='+str(target))
        return result
    except Exception as exc:
        alerts.record(db,'operator_'+type(exc).__name__,'人工CLI发送失败，结果待核实；没有自动重发。')
        raise ValueError('发送结果不确定，请核对QQ记录，不要直接重复发送')


def shell(db,cfg,api,initial):
    group=resolve_group(initial,cfg)
    print('QQ 抢话终端：直接输入即发送；/use 群名、/groups、/pause 分钟、/resume、/quit。')
    while True:
        try:
            text=input('Luna@'+group['name']+'> ').strip()
            if not text:continue
            if text=='/quit':return
            if text=='/groups':
                for g in cfg['groups']:print(str(g['id'])+' · '+g['name'])
            elif text.startswith('/use '):group=resolve_group(text[5:].strip(),cfg)
            elif text.startswith('/pause'):
                parts=text.split();pause(db,group['id'],int(parts[1]) if len(parts)>1 else 5);print('已暂停。')
            elif text=='/resume':pause(db,group['id'],0);print('已恢复。')
            elif text.startswith('/'):print('未知命令。')
            else:send(db,cfg,api,group['id'],text)
        except (EOFError,KeyboardInterrupt):print('\n已退出抢话终端；常驻服务继续运行。');return
        except Exception as exc:print('操作失败：'+str(exc))


def main():
    p=argparse.ArgumentParser(description='QQ 抢话 CLI')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('groups')
    for name in ['say','dm']:
        s=sub.add_parser(name)
        if name=='say':s.add_argument('group')
        s.add_argument('text');s.add_argument('--dry-run',action='store_true')
    s=sub.add_parser('chat');s.add_argument('group',nargs='?')
    for name in ['pause','resume']:
        s=sub.add_parser(name);s.add_argument('group')
        if name=='pause':s.add_argument('minutes',type=int,nargs='?',default=5)
    args=p.parse_args();cfg,api,db=load()
    try:
        if args.command=='groups':
            for g in cfg['groups']:print(str(g['id'])+' · '+g['name'])
        elif args.command=='dm':send(db,cfg,api,cfg['alerts']['owner_qq'],args.text,True,args.dry_run)
        elif args.command=='say':send(db,cfg,api,resolve_group(args.group,cfg)['id'],args.text,dry_run=args.dry_run)
        elif args.command=='chat':shell(db,cfg,api,args.group or api['sender']['group_id'])
        elif args.command in ['pause','resume']:pause(db,resolve_group(args.group,cfg)['id'],args.minutes if args.command=='pause' else 0);print('已更新。')
    finally:db.close()


if __name__=='__main__':
    try:main()
    except Exception as exc:print('操作失败：'+str(exc));raise SystemExit(1)


def send_image(db,cfg,api,target,raw):
    import base64,io
    from PIL import Image
    if not raw or len(raw)>15_000_000:raise ValueError('图片为空或过大')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format!='PNG' or image.width*image.height>16_000_000:raise ValueError('图片格式或尺寸不支持')
        image.verify()
    if not workflow.group_is_active(db,target,cfg):raise ValueError('群不活跃或无法发言')
    if str(agent.onebot_call(api,'get_login_info').get('user_id'))!=str(cfg['bot_id']):raise ValueError('登录账号不匹配')
    now=time.time();fp=hashlib.sha256(b'manual-image:'+raw).hexdigest();text=MANUAL_MARK+' 截图'
    outbox=ROOT/'runtime'/'outbox';outbox.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=outbox/(fp+'.png');path.write_bytes(raw);path.chmod(0o600)
    with db:ident=db.execute("INSERT INTO operator_log(target,kind,fingerprint,status,created) VALUES (?,'group-image',?,'uncertain',?)",(str(target),fp,now)).lastrowid
    try:
        result=agent.onebot_call(api,'send_group_msg',{'group_id':int(target),'message':[{'type':'text','data':{'text':text}},{'type':'image','data':{'file':'base64://'+base64.b64encode(raw).decode()}}]})
    except Exception as exc:
        alerts.record(db,'operator_image_'+type(exc).__name__,'截图发送结果待核实，不会自动重发。',scope=str(target))
        raise ValueError('截图发送结果待核实，请勿直接重试')
    with db:
        db.execute("UPDATE operator_log SET status='sent',message_id=? WHERE id=?",(str(result['message_id']),ident))
        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)',(str(target),'bot:'+str(result['message_id']),fp,text,'[]',now))
    import operator_control
    try:operator_control.owner_message(db,cfg,target,result['message_id'],text,image=raw)
    except Exception:
        import service_log
        service_log.event('surf-bot','owner image notification pending group='+str(target))
    return result
