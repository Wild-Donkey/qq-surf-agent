#!/usr/bin/env python3
"""Textual-based terminal console for surf-agent.

Optional dependency (textual) lives in runtime/deps. If import fails,
console.py falls back to the curses/lite console.

Design:
- LoginScreen: polls runtime/qq-state.json for qrcode + online flag.
- MainScreen:  left=log stream, right=group messages, bottom=command input.
- All command dispatch reuses app.console.Controller.execute().
- Tab completion reuses app.completion.candidates().
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/deps'))

import json
import os
import time

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, Button, Input, RichLog, Header, Footer
from textual.binding import Binding
from textual import work

from app import qq, completion, service_log
from app.chat_view import parse as chat_parse

# ---------------------------------------------------------------------------
# state helpers
# ---------------------------------------------------------------------------

STATE_PATH = ROOT / 'runtime' / 'qq-state.json'
CONSOLE_STATE_PATH = ROOT / 'runtime' / 'console-state.json'


def read_state():
    """Read helper_service 写入的结构化登录状态。"""
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {'online': False, 'qrcode': None, 'qrcode_url': None,
                'updated_at': None, 'last_event': None}


def helper_alive():
    pid_path = ROOT / 'runtime' / 'qq-helper-service.pid'
    try:
        pid = int(pid_path.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def load_groups():
    try:
        return json.loads((ROOT / 'workflow.json').read_text()).get('groups', [])
    except (OSError, ValueError):
        return []


def load_saved_group():
    try:
        return json.loads(CONSOLE_STATE_PATH.read_text()).get('group')
    except (OSError, ValueError):
        return None


def is_online_via_api():
    """Best-effort OneBot 登录检测（QQ 已启动且 NapCat 在线时可靠）。"""
    try:
        cfg, api, db = qq.load()
        info = qq.login_status(api)
        return bool(info[0])
    except Exception:
        return False


# ---------------------------------------------------------------------------
# command input with Tab completion + history
# ---------------------------------------------------------------------------

class CommandInput(Input):
    """输入框：Tab 补全、↑/↓ 历史，把 Tab 从焦点切换改成补全。"""

    def __init__(self, screen, **kwargs):
        super().__init__(**kwargs)
        self._screen = screen

    def key_tab(self) -> None:
        self._screen.do_complete()

    def key_up(self) -> None:
        self._screen.history_move(-1)

    def key_down(self) -> None:
        self._screen.history_move(1)


# ---------------------------------------------------------------------------
# Login screen
# ---------------------------------------------------------------------------

class LoginScreen(Screen):
    BINDINGS = [
        Binding('r', 'refresh', '刷新二维码（重启 Helper）'),
        Binding('q', 'quit', '退出'),
    ]

    # NapCat 登录码约 2 分钟失效；超过该年龄视为过期
    QR_STALE_SECONDS = 150

    CSS = """
    #login-body { height: 1fr; padding: 1 2; }
    #login-title { text-align: center; text-style: bold; color: $accent; }
    #qrcode { text-align: center; margin: 1 0; color: $text; }
    #login-status { text-align: center; color: $text-muted; height: 3; }
    #login-actions { align: center middle; height: 3; }
    #login-actions Button { margin: 0 1; }
    """

    def __init__(self):
        super().__init__()
        self._last_qr = None
        self._restarting = False
        self._last_auto_restart = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id='login-body'):
            yield Static(' Luna · QQ 登录 ', id='login-title')
            yield Static('等待二维码…', id='qrcode')
            yield Static('', id='login-status')
            with Horizontal(id='login-actions'):
                yield Button('启动 QQ Helper', id='btn-start-helper', variant='primary')
                yield Button('刷新二维码 (r)', id='btn-refresh', variant='default')
                yield Button('退出 (q)', id='btn-quit', variant='warning')
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)
        self._tick()

    def _tick(self) -> None:
        state = read_state()
        if state.get('online'):
            self.app.switch_to_main()
            return
        qr = state.get('qrcode')
        updated = state.get('updated_at') or 0
        age = time.time() - updated if updated else None
        stale = age is not None and age > self.QR_STALE_SECONDS
        qrcode_w = self.query_one('#qrcode', Static)
        if self._restarting:
            qrcode_w.update('正在重启 QQ Helper，重新获取二维码…（约 10-20 秒）')
        elif stale:
            qrcode_w.update('二维码已过期，正在自动刷新…')
        elif qr and qr != self._last_qr:
            self._last_qr = qr
            qrcode_w.update(qr)
        elif not qr:
            qrcode_w.update('（暂无二维码，启动 Helper 后会自动出现）')
        last = (state.get('last_event') or '').strip()
        pid = helper_alive()
        helper_txt = f'Helper pid={pid}' if pid else 'Helper 未运行'
        age_txt = ''
        if age is not None:
            age_txt = f'   二维码年龄 {int(age)}s' + ('（已过期）' if stale else '')
        status = f'{helper_txt}{age_txt}   {last[:70]}'
        if self._restarting:
            status = '正在重启 QQ Helper… ' + status
        self.query_one('#login-status', Static).update(status)
        self.query_one('#btn-start-helper', Button).disabled = bool(pid)
        self.query_one('#btn-refresh', Button).disabled = self._restarting
        # 过期且 helper 在跑但不再出新码：自动重启一次（有冷却）
        if stale and pid and not self._restarting:
            if time.monotonic() - self._last_auto_restart > self.QR_STALE_SECONDS:
                self._mark_restarting(True)
                self._restart_helper('auto')

    def action_refresh(self) -> None:
        # 真正的刷新：重启 QQ Helper，让 NapCat 重新出码（仅重读状态文件对过期码无效）
        if self._restarting:
            return
        self._mark_restarting(True)
        self._restart_helper('manual')

    def action_quit(self) -> None:
        self.app.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == 'btn-start-helper':
            self._start_helper()
        elif bid == 'btn-refresh':
            self.action_refresh()
        elif bid == 'btn-quit':
            self.app.exit()

    @work(exclusive=True, thread=True)
    def _start_helper(self) -> None:
        try:
            cfg, api, db = qq.load()
            msg = qq._start_helper(cfg)
        except Exception as exc:
            msg = '启动失败：' + service_log.clean(str(exc))
        self.app.call_from_thread(self._set_status, msg)

    @work(exclusive=True, thread=True)
    def _restart_helper(self, kind: str) -> None:
        try:
            cfg, api, db = qq.load()
            msg = qq.restart_helper(cfg)
        except Exception as exc:
            msg = '刷新失败：' + service_log.clean(str(exc))
        self.app.call_from_thread(self._restart_done, msg, time.monotonic())

    def _mark_restarting(self, flag: bool) -> None:
        self._restarting = flag
        self._last_qr = None
        self.query_one('#btn-refresh', Button).disabled = flag

    def _restart_done(self, msg: str, now: float) -> None:
        self._restarting = False
        self._last_auto_restart = now
        self.query_one('#btn-refresh', Button).disabled = False
        self.query_one('#login-status', Static).update(msg + '，等待 NapCat 出码…')

    def _set_status(self, msg: str) -> None:
        self.query_one('#login-status', Static).update(msg)


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------

class MainScreen(Screen):
    BINDINGS = [
        Binding('ctrl+q', 'quit', '退出'),
    ]

    CSS = """
    #svc-bar { height: 3; padding: 0 1; }
    #svc-status { width: 1fr; content-align: left middle; }
    #svc-bar Button { width: auto; min-width: 8; height: 1; margin: 1 1 0 0; }
    #main-body { height: 1fr; }
    #log-pane { width: 1fr; padding: 0 1; }
    #msg-pane { width: 1fr; padding: 0 1; }
    #log-title, #msg-title { height: 1; color: $accent; text-style: bold; padding: 0 1; }
    #log-stream, #msg-stream { height: 1fr; border: solid $primary; }
    #input-bar { height: 3; padding: 0 1; }
    #prompt { width: 3; content-align: center middle; color: $accent; }
    #cmd-input { width: 1fr; }
    """

    def __init__(self):
        super().__init__()
        self._feed = None
        self._history = []
        self._hist_idx = 0
        self._current_group = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id='svc-bar'):
            yield Static('正在检查 bot 服务…', id='svc-status', markup=False)
            yield Button('重启 bot', id='btn-bot-restart', variant='warning')
        with Horizontal(id='main-body'):
            with Vertical(id='log-pane'):
                yield Static('日志流（QQ / 应用 / 服务）', id='log-title')
                yield RichLog(id='log-stream', wrap=True, markup=False, auto_scroll=True)
            with Vertical(id='msg-pane'):
                yield Static('', id='msg-title')
                yield RichLog(id='msg-stream', wrap=True, markup=False, auto_scroll=True)
        with Horizontal(id='input-bar'):
            yield Static('>', id='prompt')
            yield CommandInput(self, placeholder='输入命令或消息，/help 查看帮助，Ctrl+Q 退出',
                               id='cmd-input')
        yield Footer()

    def on_mount(self) -> None:
        from app.terminal_feed import Feed
        self._feed = Feed(ROOT)
        self._current_group = load_saved_group()
        self._refresh_group_label()
        try:
            lines = self._feed.read()
            for line in lines[-300:]:
                self._append_line(line)
        except Exception:
            pass
        self.set_interval(0.5, self._poll_feed)
        self._service_watchdog()
        self.query_one('#cmd-input', Input).focus()

    # -- service 状态展示（生命周期归 main 层：随本终端同生共死） -------------

    @work(exclusive=True, thread=True)
    def _service_watchdog(self) -> None:
        """只读轮询 bot 服务真实状态；不在此拉起/停止任何进程。"""
        from textual.worker import get_current_worker
        from app import console as console_mod
        worker = get_current_worker()
        while not worker.is_cancelled:
            try:
                health = console_mod.service_health()
                pid = console_mod.supervisor_alive()
                owns = bool(console_mod.managed_owns)
            except Exception:
                health, pid, owns = None, None, False
            qq_online = bool(read_state().get('online'))
            n_groups = len(load_groups())
            if not worker.is_cancelled:
                self.app.call_from_thread(self._render_service, health, pid, qq_online, n_groups, owns)
            for _ in range(30):  # 3 秒分片，便于退出时尽快响应取消
                if worker.is_cancelled:
                    break
                time.sleep(0.1)

    def _render_service(self, health, pid, qq_online, n_groups, owns) -> None:
        qq_txt = 'QQ 在线' if qq_online else 'QQ 离线'
        life = '随本窗口存活（tmux 内可常驻）' if owns else '外部实例，本窗口不回收'
        if health:
            mode = health.get('mode', '?')
            groups = health.get('groups', n_groups)
            who = f'pid={pid}' if pid else '外部进程'
            text = f'● BOT 运行中  {who}  ·  模式={mode}  ·  群={groups}  ·  {qq_txt}  ·  {life}'
        else:
            text = f'○ BOT 未运行  ·  {qq_txt}  ·  {life}'
        self.query_one('#svc-status', Static).update(text)
        self.query_one('#btn-bot-restart', Button).disabled = not bool(health)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == 'btn-bot-restart':
            self._restart_bot()

    @work(exclusive=True, thread=True)
    def _restart_bot(self) -> None:
        """请求 supervisor 重启 worker（打 restart 标记，supervisor 本身不退、不换生命周期）。"""
        try:
            (ROOT / 'runtime' / 'workflow.restart').touch()
            msg = '已请求重启 bot worker（supervisor 保持运行）。'
        except Exception as exc:
            msg = '重启失败：' + service_log.clean(str(exc))
        self.app.call_from_thread(self._write_log, msg)

    def _write_log(self, text: str) -> None:
        self.query_one('#log-stream', RichLog).write(text)


    # -- group label ---------------------------------------------------------

    def _refresh_group_label(self) -> None:
        name = '未选择 (/use 群名)'
        if self._current_group:
            for g in load_groups():
                if str(g.get('id')) == str(self._current_group):
                    name = f"{g.get('name', self._current_group)} ({self._current_group})"
                    break
            else:
                name = self._current_group
        self.query_one('#msg-title', Static).update(f'群消息  |  当前群：{name}')

    # -- feed polling --------------------------------------------------------

    def _poll_feed(self) -> None:
        try:
            for line in self._feed.read():
                self._append_line(line)
        except Exception:
            pass

    def _append_line(self, line: str) -> None:
        self.query_one('#log-stream', RichLog).write(line)
        row = chat_parse(line)
        if row.group and row.group != 'private':
            self.query_one('#msg-stream', RichLog).write(row.text)

    # -- command execution ---------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        inp = self.query_one('#cmd-input', Input)
        inp.value = ''
        if not text:
            return
        if text in ('/quit', '/exit'):
            self.app.exit()
            return
        self._history.append(text)
        self._hist_idx = len(self._history)
        self.query_one('#log-stream', RichLog).write('> ' + text)
        self._run_command(text)

    @work(exclusive=True, thread=True)
    def _run_command(self, text: str) -> None:
        from app.console import Controller
        controller = Controller()
        controller.group = self._current_group
        try:
            result = controller.execute(text)
            saved = load_saved_group()
            group_changed = saved != self._current_group
            if group_changed:
                self._current_group = saved
        except Exception as exc:
            result = '操作失败：' + service_log.clean(str(exc))
            group_changed = False
        self.app.call_from_thread(self._post_result, result, group_changed)

    def _post_result(self, result: str, group_changed: bool) -> None:
        self.query_one('#log-stream', RichLog).write(result)
        if group_changed:
            self._refresh_group_label()

    # -- completion & history ------------------------------------------------

    def do_complete(self) -> None:
        inp = self.query_one('#cmd-input', Input)
        text = inp.value
        cursor = inp.cursor_position
        prefix = text[:cursor]
        choices = completion.candidates(prefix, load_groups())
        if not choices:
            return
        if len(choices) == 1:
            val = choices[0][0]
        else:
            values = [c[0] for c in choices]
            common = values[0]
            for v in values[1:]:
                while not v.startswith(common) and common:
                    common = common[:-1]
            val = common if len(common) > len(prefix) else None
        if val:
            inp.value = val + text[cursor:]
            inp.cursor_position = len(val)
        else:
            preview = '  '.join(c[0] for c in choices[:12])
            self.query_one('#log-stream', RichLog).write('补全候选：' + preview)

    def history_move(self, delta: int) -> None:
        if not self._history:
            return
        new_idx = self._hist_idx + delta
        if new_idx < 0 or new_idx > len(self._history):
            return
        self._hist_idx = new_idx
        inp = self.query_one('#cmd-input', Input)
        if new_idx < len(self._history):
            inp.value = self._history[new_idx]
            inp.cursor_position = len(inp.value)
        else:
            inp.value = ''
            inp.cursor_position = 0

    def action_quit(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class SurfConsoleApp(App):
    CSS = """
    Screen { background: $surface; }
    """
    TITLE = 'Luna Console'

    def on_mount(self) -> None:
        if is_online_via_api() or read_state().get('online'):
            self.push_screen(MainScreen())
        else:
            self.push_screen(LoginScreen())

    def switch_to_main(self) -> None:
        self.switch_screen(MainScreen())


def run() -> None:
    app = SurfConsoleApp()
    app.run()


if __name__ == '__main__':
    run()
