"""urwid-based lite console view (monochrome, high terminal compatibility).

Visual rules (measurable):
- Monochrome: all attributes use terminal default fg/bg
- Only 'accent' and 'warn' use bold for minimal structural emphasis
- No background fills anywhere -> terminal default shows through (eye-friendly)
- Single column: log stream / status bar / command input

urwid supports any terminal including legacy VT100/ANSI, with automatic
color-depth degradation. This is the high-compatibility lite backend;
prompt_toolkit (lite_view) and readline remain as fallbacks.
"""
import sys, os, json, re
from pathlib import Path

# --- optional dep lives in runtime/deps, fail fast if missing so the caller
#     (lite_console) can fall back to prompt_toolkit / readline ---
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/deps'))
import urwid  # noqa: E402  (import after sys.path tweak)

from app import completion, service_log, chat_view  # noqa: E402
from app.terminal_feed import Feed  # noqa: E402
from app.console import Controller  # noqa: E402

# ---------------------------------------------------------------------------
# Palette: monochrome. fg '' means terminal default. bg '' always (no fill).
# ---------------------------------------------------------------------------
PALETTE = [
    ('body',   '',     ''),     # default text
    ('dim',    '',     ''),     # secondary (same as body in mono)
    ('accent', 'bold', ''),     # structural markers: arrows, group tags, prompt
    ('warn',   'bold', ''),     # errors, private chat
]

MAX_LINES = 2000


_TIME_RE = re.compile(r'^\d{2}:\d{2}:\d{2}')


def format_line(raw: str):
    """Parse a raw log line and return markup list for urwid.Text."""
    row = chat_view.parse(service_log.clean(raw))
    if not row.group:
        text = row.text
        sp = text.find(' ')
        # Only split off the timestamp for actual log lines. Command output
        # ("> cmd", "executed:...", errors) has no timestamp and must stay a
        # single run so terminal output stays contiguous.
        if sp > 0 and _TIME_RE.match(text):
            return [('dim', text[:sp]), ('body', text[sp:])]
        return [('body', text)]
    text = row.text
    sp = text.find(' ')
    parts = []
    if sp > 0:
        parts.append(('dim', text[:sp]))
        rest = text[sp:]
    else:
        rest = text
    grp_end = rest.find(']')
    if grp_end > 0:
        parts.append(('accent', rest[:grp_end + 1]))
        parts.append(('body', rest[grp_end + 1:]))
    else:
        parts.append(('body', rest))
    if row.group == 'private':
        parts = [('warn', t) if a == 'accent' and '私聊' in t else (a, t) for a, t in parts]
    return parts


# ---------------------------------------------------------------------------
# Custom log widget with explicit viewport (offset from bottom).
# Unlike urwid.ListBox, appending a line does NOT change the visible window
# when the user is in history mode. This matches the original Viewport design.
# ---------------------------------------------------------------------------
class LogWidget(urwid.Widget):
    _selectable = True

    def __init__(self):
        self.lines = []            # list of markup lists
        self.offset = 0            # 0 = live (at bottom); N = N lines above bottom
        self.app = None            # set by UrwidLiteApp for status refresh

    def append(self, raw_line: str):
        self.lines.append(format_line(raw_line))
        if len(self.lines) > MAX_LINES:
            del self.lines[0]
        # offset is measured from bottom; appending does not change it,
        # so the visible window stays identical when in history mode.
        self._invalidate()

    @property
    def at_bottom(self):
        return self.offset == 0

    def scroll(self, delta_rows: int):
        end = len(self.lines) - 1
        if end < 0:
            return
        current_pos = end - self.offset
        new_pos = max(0, min(end, current_pos + delta_rows))
        self.offset = end - new_pos
        self._invalidate()

    def go_live(self):
        self.offset = 0
        self._invalidate()

    # -- urwid widget interface --------------------------------------------
    def rows(self, size, focus=False):
        return size[1] if isinstance(size, tuple) and len(size) > 1 else 1

    def render(self, size, focus=False):
        maxcol = size[0] if isinstance(size, tuple) else size
        maxrow = size[1] if isinstance(size, tuple) and len(size) > 1 else 1
        end = len(self.lines)
        start = max(0, end - maxrow - self.offset)
        visible = self.lines[start:end - self.offset]
        while len(visible) < maxrow:
            visible.insert(0, [])
        visible = visible[-maxrow:]
        markup = []
        for i, line in enumerate(visible):
            if i > 0:
                markup.append('\n')
            if line:
                markup.extend(line)
        text = urwid.Text(markup if markup else '', wrap='clip')
        return text.render((maxcol,), focus)

    def mouse_event(self, size, event, button, col, row, focus):
        if button == 4:
            self.scroll(-3)
            self._notify()
            return True
        if button == 5:
            self.scroll(3)
            self._notify()
            return True
        return False

    def _notify(self):
        if self.app:
            self.app._refresh_status()
            self.app.loop.draw_screen()


# ---------------------------------------------------------------------------
# Command input with Tab completion
# ---------------------------------------------------------------------------
class CommandEdit(urwid.Edit):
    def __init__(self, app, on_submit):
        super().__init__(('accent', '> '), '')
        self.app = app
        self.on_submit = on_submit
        self.history = []
        self.hist_idx = 0

    def keypress(self, size, key):
        if key == 'tab':
            self.app.do_complete()
            return None
        if key == 'end':
            self.app._unhandled('end')
            return None
        if key in ('page up', 'page down'):
            self.app._unhandled(key)
            return None
        if key == 'enter':
            text = self.edit_text.strip()
            self.edit_text = ''
            if text:
                self.history.append(text)
                self.hist_idx = len(self.history)
                self.on_submit(text)
            return None
        if key == 'up':
            if self.history:
                self.hist_idx = max(0, self.hist_idx - 1)
                if self.hist_idx < len(self.history):
                    self.edit_text = self.history[self.hist_idx]
                    self.edit_pos = len(self.edit_text)
            return None
        if key == 'down':
            if self.history:
                self.hist_idx = min(len(self.history), self.hist_idx + 1)
                if self.hist_idx < len(self.history):
                    self.edit_text = self.history[self.hist_idx]
                    self.edit_pos = len(self.edit_text)
                else:
                    self.edit_text = ''
            return None
        return super().keypress(size, key)


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
class UrwidLiteApp:
    def __init__(self, controller=None, root=None):
        self.root = Path(root) if root else ROOT
        self.controller = controller or Controller()
        self.feed = Feed(self.root)
        self.copy_mode = False

        self.log = LogWidget()
        self.log.app = self
        self.status = urwid.Text('')
        self.input = CommandEdit(self, self._on_submit)

        body = urwid.Pile([
            ('weight', 1, urwid.AttrMap(self.log, 'body')),
            ('pack', urwid.AttrMap(self.status, 'dim')),
            ('pack', urwid.AttrMap(self.input, 'body')),
        ])
        body.focus_position = 2
        self.top = body

        self.loop = urwid.MainLoop(
            self.top,
            palette=PALETTE,
            unhandled_input=self._unhandled,
        )

        try:
            for line in self.feed.read()[-500:]:
                self.log.append(line)
        except Exception:
            pass

    def _status_text(self):
        if self.copy_mode:
            return '复制模式 · 拖选后用终端复制快捷键 · F2 恢复'
        if not self.log.at_bottom:
            return f'历史 ↑{self.log.offset} 行 · End 回实时 · F2 复制'
        return '实时 · F2 复制 · PgUp/PgDn 翻页 · End 实时 · Tab 补全 · /quit 退出'

    def _refresh_status(self):
        self.status.set_text(self._status_text())

    def _poll_feed(self, loop, user_data):
        if self.copy_mode:
            loop.set_alarm_in(0.25, self._poll_feed)
            return
        try:
            lines = self.feed.read()
            for line in lines:
                self.log.append(line)
        except OSError:
            pass
        self._refresh_status()
        loop.set_alarm_in(0.25, self._poll_feed)

    def do_complete(self):
        text = self.input.edit_text
        cursor = self.input.edit_pos
        prefix = text[:cursor]
        try:
            groups = json.loads((self.root / 'workflow.json').read_text()).get('groups', [])
        except (OSError, ValueError):
            groups = []
        choices = completion.candidates(prefix, groups)
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
            self.input.edit_text = val + text[cursor:]
            self.input.edit_pos = len(val)
        else:
            preview = '  '.join(c[0] for c in choices[:12])
            self.log.append(f'补全候选：{preview}')

    def _on_submit(self, text):
        if text in ('/quit', '/exit'):
            raise urwid.ExitMainLoop()
        self.log.append('> ' + text)
        try:
            result = self.controller.execute(text)
        except Exception as exc:
            result = '操作失败：' + service_log.clean(str(exc))
        for line in service_log.clean(result).splitlines():
            self.log.append(line)

    def _toggle_copy(self):
        if not self.copy_mode:
            self.copy_mode = True
            self._set_mouse_tracking(False)
            self._refresh_status()
            self.loop.draw_screen()
        else:
            self.copy_mode = False
            self._set_mouse_tracking(True)
            self._refresh_status()

    def _set_mouse_tracking(self, enable):
        seq = b'\x1b[?1000h\x1b[?1006h' if enable else b'\x1b[?1000l\x1b[?1006l'
        try:
            os.write(sys.stdout.fileno(), seq)
        except OSError:
            pass

    def _unhandled(self, key):
        if key == 'f2':
            self._toggle_copy()
            return
        if key == 'ctrl c':
            if self.input.edit_text:
                self.input.edit_text = ''
            else:
                raise urwid.ExitMainLoop()
            return
        if self.copy_mode:
            return
        if key == 'page up':
            self.log.scroll(-10)
            self._refresh_status()
            return
        if key == 'page down':
            self.log.scroll(10)
            self._refresh_status()
            return
        if key == 'end':
            self.log.go_live()
            self._refresh_status()
            return

    def run(self):
        self._refresh_status()
        self.loop.set_alarm_in(0.1, self._poll_feed)
        self.loop.run()


def run(controller=None, root=None, completer=None):
    """Entry point matching lite_view.run(controller, root, completer).

    `completer` is accepted for signature compatibility but unused — urwid
    drives completion directly via app.completion.candidates.
    """
    app = UrwidLiteApp(controller=controller, root=root)
    try:
        app.run()
    except (KeyboardInterrupt, EOFError):
        pass
