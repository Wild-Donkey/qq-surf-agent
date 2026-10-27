#!/usr/bin/env python3
"""QQ operator CLI: talk as the bot and temporarily take the floor."""
import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
import time
from app import agent
from app import alerts
from app import service_log
from app import workflow
ROOT=Path(__file__).resolve().parents[1]


def load():
    cfg=json.loads((ROOT/'workflow.json').read_text());api=json.loads((ROOT/'config.json').read_text())
    path=Path(cfg['database'])
    if not path.is_absolute():path=ROOT/path
    path.parent.mkdir(parents=True,exist_ok=True)
    return cfg,api,workflow.connect(path)


def _helper_pid():
    pidf=ROOT/'runtime'/'qq-helper-service.pid'
    try:
        pid=int(pidf.read_text());os.kill(pid,0);return pid
    except (OSError,ValueError):return None


def _start_helper(cfg):
    """启动 QQ Helper（容器 HOME，loadNapCat.js 已打补丁则不带 --no-sandbox）。"""
    if _helper_pid():return 'QQ Helper 已在运行，pid='+str(_helper_pid())
    work=ROOT/'runtime';work.mkdir(exist_ok=True)
    (work/'qq-helper.lock').unlink(missing_ok=True)
    (work/'qq-helper-service.pid').unlink(missing_ok=True)
    (work/'qq-helper-child.pid').unlink(missing_ok=True)
    command=cfg.get('console',{}).get('helper_command',['/Applications/QQ.app/Contents/MacOS/QQ','-q',str(cfg['bot_id'])])
    # 沙箱版 QQ 必须用容器 HOME，否则 NapCat 写不了数据目录
    env=os.environ.copy()
    env['HOME']=str(Path.home()/'Library'/'Containers'/'com.tencent.qq'/'Data')
    with (work/'console-services.log').open('ab') as out:
        proc=subprocess.Popen([sys.executable,'-u','-m','app.helper_service','--',*map(str,command)],
                              cwd=ROOT,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,
                              start_new_session=True,env=env)
    return 'QQ Helper 启动中，pid='+str(proc.pid)


def stop_helper(timeout=15):
    """停止受控制台管理的 QQ Helper（SIGTERM service，由它带走 QQ 子进程）。

    返回 (旧pid或None, 消息)。仅管理 runtime/qq-helper-service.pid 记录的实例，
    不会终止用户手动启动的外部 QQ。
    """
    pid=_helper_pid()
    if not pid:
        return None,'没有受管理的 QQ Helper'
    try:
        os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline=time.time()+timeout
    while time.time()<deadline:
        if not _helper_pid():
            return pid,'QQ Helper 已停止'
        time.sleep(.3)
    raise TimeoutError('QQ Helper 停止超时（%d 秒），pid=%d'%(timeout,pid))


def restart_helper(cfg,timeout=20,retries=10):
    """停止并重新启动 QQ Helper，强制 NapCat 重新生成登录二维码。

    pid 文件先于 flock 释放，启动后需确认新 service 进程存活；
    若因锁冲突瞬间退出则重试。
    """
    stop_helper(timeout=timeout)
    last='QQ Helper 启动中'
    for _ in range(retries):
        if _helper_pid():
            time.sleep(.5);continue
        last=_start_helper(cfg)
        time.sleep(.8)
        if _helper_pid():
            return last
        time.sleep(.5)
    return last


def _latest_qrcode():
    """从 runtime/qq-state.json 读取最新二维码（helper_service 实时解析写入）。"""
    path=ROOT/'runtime'/'qq-state.json'
    try:
        state=json.loads(path.read_text())
        return state.get('qrcode'),state.get('qrcode_url')
    except (OSError,ValueError):
        return None,None


def login_status(api):
    """返回 (online:bool, info:dict|None)。"""
    try:
        info=agent.onebot_call(api,'get_login_info')
        return True,info
    except Exception:
        return False,None


def login_flow(cfg,api,timeout=600):
    """交互式 QQ 登录引导：检查状态 → 启动 Helper → 显示二维码 → 轮询直到成功。
    支持按回车手动刷新二维码，按 q 退出。返回成功消息字符串。"""
    import select
    online,info=login_status(api)
    if online:
        return 'QQ 已登录：%s (%s)'%(info.get('nickname'),info.get('user_id'))
    # 未登录，确保 Helper 在运行
    msg=_start_helper(cfg)
    print(msg,flush=True)
    deadline=time.time()+timeout
    last_qr=None
    print('\n等待 NapCat 生成二维码...\n',flush=True)
    while time.time()<deadline:
        online,info=login_status(api)
        if online:
            print('\n登录成功：%s (%s)'%(info.get('nickname'),info.get('user_id')),flush=True)
            return '登录成功：%s (%s)'%(info.get('nickname'),info.get('user_id'))
        block,url=_latest_qrcode()
        if block and block!=last_qr:
            print(block,flush=True)
            print('\n[回车] 重启 QQ Helper 获取新二维码  [q] 退出   （二维码约2分钟过期）\n',flush=True)
            last_qr=block
        # 非阻塞检测用户输入
        ready,_,_=select.select([sys.stdin],[],[],0.5)
        if ready:
            try:ch=sys.stdin.readline().strip()
            except OSError:ch=''
            if ch.lower()=='q':
                return '已取消登录'
            # 回车：重启 Helper 强制 NapCat 重新出码（仅重读状态文件对过期码无效）
            print('正在重启 QQ Helper，重新获取二维码…',flush=True)
            try:
                restart_helper(cfg)
                last_qr=None
            except Exception as exc:
                print('重启失败：'+str(exc),flush=True)
    return '登录超时（%d秒），请重试' % timeout


def resolve_group(query,cfg):
    query=str(query).strip()
    exact=[g for g in cfg['groups'] if g.get('enabled',True) and (str(g['id'])==query or g['name'].casefold()==query.casefold() or (g['name']+' ['+str(g['id'])+']').casefold()==query.casefold())]
    if len(exact)==1:return exact[0]
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
            from app import operator_control
            try:operator_control.owner_message(db,cfg,target,result['message_id'],text)
            except Exception:
                from app import service_log
                service_log.event('surf-bot','owner notification pending group='+str(target))
        return result
    except Exception as exc:
        detail=type(exc).__name__+': '+str(exc)[:200]
        if hasattr(exc,'retcode'):detail+=' retcode='+str(exc.retcode)
        if hasattr(exc,'wording') and exc.wording:detail+=' wording='+str(exc.wording)[:100]
        alerts.record(db,'operator_'+type(exc).__name__,'人工CLI发送失败：'+detail+'；结果待核实，没有自动重发。')
        with db:db.execute("UPDATE operator_log SET status='failed',error=? WHERE id=?",(detail[:300],ident))
        raise ValueError('发送结果不确定：'+detail+'。请核对QQ记录，不要直接重复发送')


RECALL_WINDOW_SECONDS = 120

def recall(db, cfg, api, target, message_id=None):
    """撤回本群内bot最近两分钟内发送的消息。

    message_id 为空时撤回最近一条；指定时仅撤回该条（仍受两分钟时限约束）。
    同时覆盖自动投递（deliveries）与人工接管（operator_log）两类来源。
    """
    now = time.time()
    target = str(target)
    login = agent.onebot_call(api, 'get_login_info')
    if str(login.get('user_id')) != str(cfg['bot_id']):
        raise ValueError('登录账号与bot不一致')
    window = now - RECALL_WINDOW_SECONDS
    if message_id is not None:
        mid = str(message_id).removeprefix('bot:')
        delivery = db.execute(
            "SELECT message_id, updated FROM deliveries WHERE target_group=? AND message_id=? AND status='sent'",
            (target, mid)).fetchone()
        op_log = db.execute(
            "SELECT message_id, created FROM operator_log WHERE target=? AND message_id=? AND status='sent' AND kind IN ('group','group-image')",
            (target, mid)).fetchone()
        if not delivery and not op_log:
            raise ValueError('找不到该消息或非bot在本群发送')
        sent_at = delivery['updated'] if delivery else op_log['created']
        if now - sent_at > RECALL_WINDOW_SECONDS:
            raise ValueError('该消息已超过两分钟撤回时限')
        chosen = mid
    else:
        delivery = db.execute(
            "SELECT message_id, updated FROM deliveries WHERE target_group=? AND status='sent' AND message_id IS NOT NULL AND updated>? ORDER BY updated DESC LIMIT 1",
            (target, window)).fetchone()
        op_log = db.execute(
            "SELECT message_id, created FROM operator_log WHERE target=? AND status='sent' AND message_id IS NOT NULL AND kind IN ('group','group-image') AND created>? ORDER BY created DESC LIMIT 1",
            (target, window)).fetchone()
        candidates = []
        if delivery:
            candidates.append((delivery['message_id'], delivery['updated']))
        if op_log:
            candidates.append((op_log['message_id'], op_log['created']))
        if not candidates:
            raise ValueError('近两分钟内没有可撤回的bot消息')
        chosen, _ = max(candidates, key=lambda x: x[1])
    agent.onebot_call(api, 'delete_msg', {'message_id': int(chosen)})
    with db:
        db.execute("UPDATE deliveries SET status='recalled', updated=? WHERE target_group=? AND message_id=? AND status='sent'",
                   (now, target, chosen))
        db.execute("UPDATE operator_log SET status='recalled' WHERE target=? AND message_id=? AND status='sent'",
                   (target, chosen))
    service_log.event('surf-bot', 'recalled group=' + target + ' message_id=' + str(chosen))
    return str(chosen)


def shell(db,cfg,api,initial):
    group=resolve_group(initial,cfg)
    print('QQ 抢话终端：直接输入即发送；/use 群名、/groups、/pause 分钟、/resume、/recall [消息ID]、/quit。')
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
            elif text.startswith('/recall'):
                parts=text.split();mid=parts[1] if len(parts)>1 else None
                recalled=recall(db,cfg,api,group['id'],mid)
                print('已撤回消息：'+recalled)
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
    s=sub.add_parser('recall');s.add_argument('group');s.add_argument('message_id',nargs='?')
    args=p.parse_args();cfg,api,db=load()
    try:
        if args.command=='groups':
            for g in cfg['groups']:print(str(g['id'])+' · '+g['name'])
        elif args.command=='dm':send(db,cfg,api,cfg['alerts']['owner_qq'],args.text,True,args.dry_run)
        elif args.command=='say':send(db,cfg,api,resolve_group(args.group,cfg)['id'],args.text,dry_run=args.dry_run)
        elif args.command=='chat':shell(db,cfg,api,args.group or api['sender']['group_id'])
        elif args.command in ['pause','resume']:pause(db,resolve_group(args.group,cfg)['id'],args.minutes if args.command=='pause' else 0);print('已更新。')
        elif args.command=='recall':
            recalled=recall(db,cfg,api,resolve_group(args.group,cfg)['id'],args.message_id)
            print('已撤回消息：'+recalled)
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
    from app import operator_control
    try:operator_control.owner_message(db,cfg,target,result['message_id'],text,image=raw)
    except Exception:
        from app import service_log
        service_log.event('surf-bot','owner image notification pending group='+str(target))
    return result
