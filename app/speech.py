"""Local bounded ASR; only NapCat-returned WAV bytes enter the recognizer."""
import base64,concurrent.futures,hashlib,json,subprocess,sys,wave
from pathlib import Path
from app import agent
ROOT=Path(__file__).resolve().parents[1]
_POOL=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='private-speech')
_PENDING={}

def recognize(api,metadata,root):
 ident=metadata.get('file')
 if not isinstance(ident,str) or not ident or len(ident)>4096:raise ValueError('invalid_voice_id')
 result=agent.onebot_call(api,'get_record',{'file':ident,'out_format':'wav'})
 encoded=result.get('base64','')
 if not isinstance(encoded,str) or not 0<len(encoded)<=14_000_000:raise ValueError('voice_size_or_data')
 raw=base64.b64decode(encoded,validate=True)
 if not raw.startswith(b'RIFF') or raw[8:12]!=b'WAVE':raise ValueError('voice_not_wav')
 directory=Path(root)/'private-audio';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
 key=hashlib.sha256(raw).hexdigest();wav=directory/(key+'.wav');out=directory/(key+'.json')
 if out.exists():return json.loads(out.read_text())['text']
 wav.write_bytes(raw);wav.chmod(0o600)
 with wave.open(str(wav)) as audio:
  duration=audio.getnframes()/audio.getframerate()
 if not 0<duration<=180:raise ValueError('voice_duration_limit')
 run=subprocess.run([sys.executable,str(ROOT/'app.speech.py'),'--recognize',str(wav)],capture_output=True,text=True,timeout=90)
 if run.returncode:raise ValueError('local_asr_failed')
 text=json.loads(run.stdout)['text'].strip()[:6000]
 if not text:raise ValueError('voice_unintelligible')
 out.write_text(json.dumps({'text':text},ensure_ascii=False));out.chmod(0o600)
 return text

def prepare(db,batch,api,cfg):
 """Return false while transcription runs; group processing remains available."""
 ready=True
 for row in batch:
  media=json.loads(row['media']);voices=[(i,m) for i,m in enumerate(media) if m.get('_type')=='record' and not m.get('_transcript')]
  if not voices:continue
  additions=[];changed=False
  for i,meta in voices:
   key=(row['user_id'],row['message_id'],i)
   future=_PENDING.get(key)
   if future is None:
    # One active message at a time prevents an unbounded executor backlog.
    if len(_PENDING)>=4:ready=False;continue
    future=_POOL.submit(recognize,api,meta,cfg['context_dir']);_PENDING[key]=future
   if not future.done():ready=False;continue
   try:text=future.result();additions.append('[语音转写，可能有误] '+text);media[i]['_transcript']=text
   except Exception as exc:
    media[i]['_transcript']='[语音无法识别]';additions.append('[语音无法识别，请明确说明没听清，不要猜测内容]')
    import alerts
    alerts.record(db,'voice_transcription_failed','部分私信语音无法转写。',scope=row['user_id'],details={'error':type(exc).__name__})
   del _PENDING[key];changed=True
  if changed:
   with db:db.execute('UPDATE private_messages SET text=?,media=? WHERE user_id=? AND message_id=?',(row['text']+'\n'+'\n'.join(additions),json.dumps(media),row['user_id'],row['message_id']))
 return ready

if __name__=='__main__':
 sys.path.insert(0,str(ROOT/'runtime'/'speech-deps'))
 from faster_whisper import WhisperModel
 model=WhisperModel('base',device='cpu',compute_type='int8',cpu_threads=4,download_root=str(ROOT/'runtime'/'speech-models'),local_files_only=True)
 segments,info=model.transcribe(sys.argv[2],beam_size=3,vad_filter=True,condition_on_previous_text=False)
 print(json.dumps({'text':''.join(s.text for s in segments)},ensure_ascii=False))
