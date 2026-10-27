import os,pty,subprocess,tempfile,select,time,sys,json,fcntl,struct,termios
from pathlib import Path
root=Path(__file__).resolve().parents[1];python=sys.executable

import unittest

@unittest.skipUnless(os.name=="posix", "requires POSIX PTY")
class LitePTYTest(unittest.TestCase):
 def test_typing_does_not_block_logs_and_completion(self):
  if not (root/"runtime/deps/prompt_toolkit").exists():self.skipTest("optional lite dependency unavailable")
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'runtime/logs').mkdir(parents=True);log=p/'runtime/logs/qq-helper.log';log.write_text(''.join('10-21 16:00:00 [info] bot | 接收 <- 群聊 [测试群(1)] [测试人(2)] history-'+str(n)+'\n' for n in range(120)));(p/'workflow.json').write_text(json.dumps({'groups':[{'id':1,'name':'测试群'}]}))
   code="import sys;from pathlib import Path;from app import lite_console;lite_console.ROOT=Path(sys.argv[1]);lite_console.run(controller=type('C',(),{'execute':lambda self,line:'executed:'+line})())"
   master,slave=pty.openpty()
   fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',40,120,0,0))
   env=dict(os.environ,TERM='xterm-256color',PROMPT_TOOLKIT_NO_CPR='1',SURF_LOG_DIR='/tmp/surf-console-test-logs')
   child=subprocess.Popen([python,'-u','-c',code,tmp],stdin=slave,stdout=slave,stderr=slave,env=env,cwd=root);os.close(slave);data=b''
   def read_for(seconds):
    nonlocal data
    until=time.time()+seconds
    while time.time()<until:
     if select.select([master],[],[],.1)[0]:
      try:data+=os.read(master,65536)
      except OSError:break
   try:
    read_for(1);os.write(master,b'/use ');read_for(.2)
    with log.open('a') as f:f.write('10-21 16:00:01 [info] bot | 接收 <- 群聊 [测试群(1)] [测试人(2)] realtime-while-typing\n')
    notes={'summary':'窗口理解已经显示','images':[],'uncertainties':[]}
    (p/'runtime/logs/surf-bot.log').write_text('10-21 16:00:01 worker understanding group=1 request_id=sample observations='+json.dumps(notes,ensure_ascii=False)+'\n')
    read_for(1)
    assert '窗口理解已经显示' in data.decode(errors='replace'),'application understanding missing from lite stream'
    assert b'realtime-while-typing' in data,repr(data[-5000:])
    os.write(master,b'\x1bOQ');start=len(data);read_for(.5)
    assert '复制模式' in data[start:].decode(errors='replace'),'F2 copy mode missing'
    assert b'\x1b[?1000l' in data[start:],'mouse tracking still enabled during selection'
    with log.open('a') as f:f.write('10-21 16:00:02 [info] bot | 接收 <- 群聊 [测试群(1)] [测试人(2)] copy-mode-postponed\n')
    start=len(data);read_for(.5)
    assert b'copy-mode-postponed' not in data[start:],'copy selection interrupted by log refresh'
    os.write(master,b'\x1bOQ');start=len(data);read_for(.8)
    assert b'copy-mode-postponed' in data[start:],'logs did not resume after copy mode'
    assert b'\x1b[?1000h' in data[start:],'wheel tracking not restored'
    os.write(master,b'\x1b[5~');start=len(data);read_for(.5)
    assert '历史' in data[start:].decode(errors='replace'),'PageUp did not enter history'
    with log.open('a') as f:f.write('10-21 16:00:02 [info] bot | 接收 <- 群聊 [测试群(1)] [测试人(2)] held-until-live\n')
    start=len(data);read_for(.6)
    assert b'held-until-live' not in data[start:],'new message pulled history to bottom'
    os.write(master,b'\x1b[F');read_for(.5)
    assert b'eld-until-live' in data,repr(data[-4000:])
    # SGR wheel events over blank space must also scroll both directions.
    os.write(master,b'\x1b[<64;75;5M');start=len(data);read_for(.5)
    assert '历史' in data[start:].decode(errors='replace'),'wheel up failed'
    os.write(master,b'\x1b[<65;75;5M');start=len(data);read_for(.5)
    assert '实时' in data[start:].decode(errors='replace'),'wheel down failed'
    os.write(master,b'\t');read_for(.3);os.write(master,b'\r');read_for(.8)
    assert 'executed:/use 测试群' in data.decode(errors='replace'),'completion/input not preserved'
    os.write(master,b'/quit\r');read_for(1);child.wait(timeout=3)
    print('PTY verified: live logs during typing, group-name completion, command buffer preserved, PageUp/End, bidirectional SGR wheel, anchored history, normal exit')
   finally:
    if child.poll() is None:child.kill();child.wait()
    os.close(master)
