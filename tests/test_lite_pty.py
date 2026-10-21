import os,pty,subprocess,tempfile,select,time,sys,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];python=sys.executable

import unittest

@unittest.skipUnless(os.name=="posix", "requires POSIX PTY")
class LitePTYTest(unittest.TestCase):
 def test_typing_does_not_block_logs_and_completion(self):
  if not (root/"runtime/deps/prompt_toolkit").exists():self.skipTest("optional lite dependency unavailable")
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'runtime/logs').mkdir(parents=True);log=p/'runtime/logs/qq-helper.log';log.write_text('');(p/'workflow.json').write_text(json.dumps({'groups':[{'id':1,'name':'测试群'}]}))
   code="import sys;from pathlib import Path;import lite_console;lite_console.ROOT=Path(sys.argv[1]);lite_console.run(controller=type('C',(),{'execute':lambda self,line:'executed:'+line})())"
   master,slave=pty.openpty();env=dict(os.environ,TERM='xterm-256color',PROMPT_TOOLKIT_NO_CPR='1',SURF_LOG_DIR='/tmp/surf-console-test-logs')
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
    read_for(1)
    assert b'realtime-while-typing' in data,repr(data[-5000:])
    os.write(master,b'\t');read_for(.3);os.write(master,b'\r');read_for(.8)
    assert 'executed:/use 测试群' in data.decode(errors='replace'),'completion/input not preserved'
    os.write(master,b'/quit\r');child.wait(timeout=3)
    print('PTY verified: live logs during typing, group-name completion, command buffer preserved, normal exit')
   finally:
    if child.poll() is None:child.kill();child.wait()
    os.close(master)
