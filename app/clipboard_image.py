"""Read a local macOS clipboard image; never executes pasted text."""
import io,subprocess,tempfile
from pathlib import Path
SCRIPT='''on run argv
set outPath to item 1 of argv
set imageType to item 2 of argv
if imageType is "png" then
set imageData to the clipboard as «class PNGf»
else
set imageData to the clipboard as «class TIFF»
end if
set outFile to open for access POSIX file outPath with write permission
try
set eof outFile to 0
write imageData to outFile
close access outFile
on error errorMessage
try
close access outFile
end try
error errorMessage
end try
end run'''

def read_png():
 from PIL import Image
 with tempfile.TemporaryDirectory(prefix='surf-clipboard-') as tmp:
  path=Path(tmp)/'image'
  for fmt in ('png','tiff'):
   result=subprocess.run(['osascript','-',str(path),fmt],input=SCRIPT,text=True,capture_output=True,timeout=10)
   if result.returncode:continue
   if not path.exists() or path.stat().st_size>15_000_000:raise ValueError('剪贴板图片过大，最多15MB')
   try:
    with Image.open(path) as image:
     if image.width*image.height>16_000_000:raise ValueError('图片尺寸过大，最多1600万像素')
     image=image.convert('RGBA');out=io.BytesIO();image.save(out,format='PNG');raw=out.getvalue()
    if len(raw)>15_000_000:raise ValueError('转换后的图片过大')
    return raw
   except (OSError,SyntaxError):continue
 raise ValueError('剪贴板里没有可读取的图片；请先复制图片或截图')
