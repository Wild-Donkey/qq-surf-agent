import io,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import vision,chatgpt_auth

class VisionTests(unittest.TestCase):
    def test_only_qq_image_hosts_allowed(self):
        self.assertTrue(vision.allowed('https://multimedia.nt.qq.com.cn/download?x=1'))
        self.assertTrue(vision.allowed('https://gchat.qpic.cn/a'))
        self.assertTrue(vision.allowed('https://gxh.vip.qq.com/a'))
        for url in ['http://gchat.qpic.cn/a','https://127.0.0.1/a','https://qpic.cn.evil.example/a','file:///tmp/a','https://gchat.qpic.cn:8443/a']:
            self.assertFalse(vision.allowed(url))
    def test_images_are_real_inputs_and_urls_are_not_in_text_payload(self):
        payload={'unread_messages':[{'text':'看看这个'}],'_vision':[{'reference':'message_1_image_1','url':'https://gchat.qpic.cn/private-signed-url'}]}
        with patch('app.vision.fetch',return_value=(b'jpeg-data',False)):
            clean,images=vision.prepare(payload)
        self.assertNotIn('private-signed-url',json.dumps(clean))
        self.assertEqual(images[1]['type'],'input_image')
        self.assertTrue(images[1]['image_url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(clean['vision_status']['attached'][0]['reference'],'message_1_image_1')
    def test_unreadable_image_is_explicit_not_silently_claimed(self):
        with patch('app.vision.fetch',side_effect=ValueError('unavailable')):
            clean,images=vision.prepare({'_vision':[{'reference':'img','url':'https://gchat.qpic.cn/a'}]})
        self.assertFalse(images);self.assertEqual(clean['vision_status']['unavailable'][0]['reference'],'img')
    def test_responses_receives_multimodal_content(self):
        event={'type':'response.completed','response':{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'image understood'}]}]}}
        stream=io.BytesIO(('data: '+json.dumps(event)+'\n\n').encode())
        inputs=[{'type':'input_image','image_url':'data:image/png;base64,AAAA','detail':'high'}]
        with patch('app.chatgpt_auth.access_token',return_value='test'),patch('app.chatgpt_auth.urllib.request.urlopen',return_value=stream) as request:
            chatgpt_auth.infer('selected','rules',{'text':'test'},inputs)
        body=json.loads(request.call_args[0][0].data)
        self.assertEqual(body['input'][0]['content'][1]['type'],'input_image')
    def test_decoder_accepts_real_png_and_rejects_svg(self):
        try:from PIL import Image
        except ImportError:self.skipTest('optional Pillow dependency not installed')
        image=Image.new('RGB',(64,64),'red');data=io.BytesIO();image.save(data,format='PNG')
        output,animated=vision.decode_image(data.getvalue())
        self.assertTrue(output.startswith(b'\xff\xd8'));self.assertFalse(animated)
        with self.assertRaises(Exception):vision.decode_image(b'<svg xmlns="http://www.w3.org/2000/svg"/>')

    def test_expired_url_falls_back_to_local_asset(self):
        with patch('app.vision.fetch',side_effect=ValueError('expired')),patch('app.vision.local_image',return_value=(b'jpeg',False)) as local:
            self.assertEqual(vision.from_metadata({'url':'https://gchat.qpic.cn/expired','path':'/cache/image'}),(b'jpeg',False))
        local.assert_called_once_with('/cache/image')
    def test_direct_metadata_path_is_used_without_bridge_lookup(self):
        with patch('app.vision.local_image',return_value=(b'jpeg',False)),patch('app.vision.bridge_call') as bridge:
            self.assertEqual(vision.resolve({'metadata':{'path':'/cache/image'}}),(b'jpeg',False))
        bridge.assert_not_called()
    def test_large_normal_screenshot_is_resized_before_conversion(self):
        from PIL import Image
        data=io.BytesIO();Image.new('RGB',(4200,4000),'white').save(data,format='PNG')
        output,_=vision.decode_image(data.getvalue())
        with Image.open(io.BytesIO(output)) as image:self.assertLessEqual(max(image.size),1600)

    def test_phone_mpo_image_decodes_first_frame(self):
        from PIL import Image
        data=io.BytesIO();Image.new('RGB',(64,64),'red').save(data,format='MPO',save_all=True,append_images=[Image.new('RGB',(64,64),'blue')])
        output,_=vision.decode_image(data.getvalue())
        self.assertTrue(output.startswith(b'\xff\xd8'))
