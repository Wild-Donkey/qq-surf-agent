import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import chatgpt_auth as auth
import social


class ChatGPTTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.private=patch.object(auth,'PRIVATE',Path(self.tmp.name));self.private.start()
        self.pending={'state':'s','nonce':'n','verifier':'v','host_id':'h','redirect_uri':'http://127.0.0.1:8768/auth/callback','expires_at':time.time()+60}
        self.tokens={'access_token':'access','refresh_token':'refresh','id_token':'id','expires_in':3600,'scope':auth.SCOPE}
    def tearDown(self):self.private.stop();self.tmp.cleanup()
    def finish(self):
        with patch.object(auth,'request_json',return_value=self.tokens),patch.object(auth,'verify_identity',return_value={'sub':'user'}):
            auth.complete({'state':'s','code':'c','client_id':'issued'},self.pending)
    def test_invalid_callback_never_exchanges_code(self):
        for params in [{'state':'wrong','code':'c','client_id':'issued'}, {'state':'s','error':'access_denied'}, {'state':'s','code':'c'}]:
            with patch.object(auth,'request_json') as request:
                with self.assertRaises(ValueError):auth.complete(params,self.pending)
                request.assert_not_called()
    def test_no_plan_permission_preserves_previous_connection(self):
        self.finish();old=auth.active_record()[1]
        tokens=dict(self.tokens,scope='openid')
        with patch.object(auth,'request_json',return_value=tokens),patch.object(auth,'verify_identity',return_value={'sub':'other'}):
            with self.assertRaises(ValueError):auth.complete({'state':'s','code':'c','client_id':'another'},self.pending)
        self.assertEqual(auth.active_record()[1],old)
    def test_identity_validation_failure_stores_nothing(self):
        with patch.object(auth,'request_json',return_value=self.tokens),patch.object(auth,'verify_identity',side_effect=ValueError('bad signature')):
            with self.assertRaises(ValueError):auth.complete({'state':'s','code':'c','client_id':'issued'},self.pending)
        self.assertFalse(auth.available())
    def test_signed_identity_rejects_wrong_nonce_audience_expiry_and_signature(self):
        sys.path.insert(0,str(auth.ROOT/'runtime'/'deps'))
        try:
            import jwt
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError:self.skipTest('optional ChatGPT login dependencies not installed')
        from types import SimpleNamespace
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        wrong=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        claims={'sub':'user','iss':auth.AUTH,'aud':'issued','nonce':'n','iat':int(time.time()),'exp':int(time.time()+60)}
        discovery={'issuer':auth.AUTH,'jwks_uri':auth.AUTH+'/.well-known/jwks.json'}
        with patch.object(auth,'request_json',return_value=discovery),patch.object(jwt,'PyJWKClient') as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value=SimpleNamespace(key=key.public_key())
            self.assertEqual(auth.verify_identity({'id_token':jwt.encode(claims,key,algorithm='RS256')},'issued','n')['sub'],'user')
            for changed,signer in [(dict(claims,nonce='wrong'),key),(dict(claims,aud='another'),key),(dict(claims,exp=int(time.time()-60)),key),(dict(claims,iss='https://evil.example'),key),(claims,wrong)]:
                with self.assertRaises((ValueError,jwt.PyJWTError)):
                    auth.verify_identity({'id_token':jwt.encode(changed,signer,algorithm='RS256')},'issued','n')
    def test_atomic_private_storage_and_rotating_refresh(self):
        self.finish();path,record=auth.active_record()
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        record['expires_at']=0;auth.save(path,record)
        tokens=dict(self.tokens,access_token='new-access',refresh_token='new-refresh')
        with patch.object(auth,'request_json',return_value=tokens) as request:
            self.assertEqual(auth.access_token(),'new-access')
            self.assertEqual(request.call_args[0][1]['client_id'],'issued')
        self.assertEqual(auth.active_record()[1]['refresh_token'],'new-refresh')
    def test_stream_must_complete_and_uses_public_responses(self):
        import io
        data={'type':'response.completed','response':{'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"action":"wait"}'}]}],'usage':{'input_tokens':12}}}
        stream=io.BytesIO(('data: '+json.dumps(data)+'\n\n').encode())
        with patch.object(auth,'access_token',return_value='secret'),patch.object(auth.urllib.request,'urlopen',return_value=stream) as request:
            text,usage=auth.infer('selected','rules',{'context':'sample'})
            req=request.call_args[0][0];body=json.loads(req.data)
            self.assertEqual(req.full_url,auth.RESOURCE+'/responses')
            self.assertFalse(body['store']);self.assertTrue(body['stream'])
            self.assertNotIn('max_output_tokens',body)
            self.assertEqual(usage['prompt_tokens'],12)
        with patch.object(auth,'access_token',return_value='secret'),patch.object(auth.urllib.request,'urlopen',return_value=io.BytesIO(b'')):
            with self.assertRaises(ValueError):auth.infer('selected','rules',{})
    def test_planner_uses_oauth_adapter(self):
        with patch.object(auth,'available',return_value=True),patch.object(auth,'infer',return_value=('{"action":"wait","text":""}',{})):
            result,_=social.call_model({'enabled':True,'provider':'chatgpt','model':'selected'}, {})
            self.assertEqual(result['action'],'wait')


if __name__=='__main__':unittest.main()
