import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import agent


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.db = agent.connect(':memory:')
        self.cfg = {'max_send_per_run': 3, 'send_interval_seconds': 1}

    def tearDown(self):
        self.db.close()

    def test_rss_and_atom(self):
        rss = b'<rss><channel><item><title>A &amp; B</title><link>https://example.com/a?utm_source=x</link><description>&lt;b&gt;hello&lt;/b&gt;</description></item></channel></rss>'
        self.assertEqual(agent.parse_feed(rss)[0], {'title': 'A & B', 'url': 'https://example.com/a', 'summary': 'hello'})
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Test</title><link rel="self" href="https://example.com/feed"/><link href="https://example.com/post"/></entry></feed>'
        self.assertEqual(agent.parse_feed(atom)[0]['url'], 'https://example.com/post')

    def test_image_extraction_and_unsafe_scheme(self):
        rss = b'<rss><channel><item><title>comic</title><link>https://example.com/a</link><description>&lt;img src="file:///private/image"&gt;&lt;img src="https://example.com/comic.png"&gt;</description></item></channel></rss>'
        self.assertEqual(agent.parse_feed(rss)[0]['image_url'], 'https://example.com/comic.png')

    def test_duplicate_and_preview(self):
        agent.collect(self.db, self.cfg, True)
        agent.collect(self.db, self.cfg, True)
        self.assertEqual(self.db.execute('select count(*) from items').fetchone()[0], 2)
        self.db.execute("update items set status='approved'")
        with patch('app.agent.request') as network:
            agent.send(self.db, self.cfg)
            network.assert_not_called()
        self.assertEqual(self.db.execute("select count(*) from items where status='approved'").fetchone()[0], 2)

    def test_ambiguous_send_is_not_retried(self):
        agent.collect(self.db, self.cfg, True)
        self.db.execute("update items set status='approved' where id=1")
        self.cfg['sender'] = {'mode': 'bridge', 'url': 'http://localhost/send', 'group_id': 'test'}
        with patch('app.agent.request', side_effect=TimeoutError):
            agent.send(self.db, self.cfg)
        self.assertEqual(self.db.execute('select status from items where id=1').fetchone()[0], 'uncertain')
        with patch('app.agent.request') as network:
            agent.send(self.db, self.cfg)
            network.assert_not_called()

    def test_model_response_and_failure(self):
        self.cfg['llm'] = {'enabled': True, 'require_key': False, 'base_url': 'http://localhost/v1', 'model': 'test'}
        response = {'choices': [{'message': {'content': '{"keep":true,"caption":"好玩"}'}}]}
        with patch('app.agent.request', return_value=json.dumps(response).encode()):
            text, keep = agent.caption({'url': 'https://example.com/a'}, self.cfg)
            self.assertTrue(keep)
            self.assertEqual(text, '好玩\nhttps://example.com/a')
        with patch('app.agent.request', return_value=b'{}'):
            agent.collect(self.db, self.cfg, True)
        self.assertEqual(self.db.execute('select count(*) from items').fetchone()[0], 0)

    def test_browser_import_deduplicates_and_filters(self):
        self.cfg['exclude_keywords'] = ['广告']
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'candidates.json'
            path.write_text(json.dumps([
                {'title': '有趣帖子', 'url': 'https://example.com/post?utm_source=a'},
                {'title': '相同帖子', 'url': 'https://example.com/post'},
                {'title': '广告', 'url': 'https://example.com/ad'}]), encoding='utf-8')
            agent.import_candidates(self.db, self.cfg, path)
        self.assertEqual(self.db.execute('select count(*) from items').fetchone()[0], 1)
        self.assertEqual(self.db.execute('select status from items').fetchone()[0], 'pending')


if __name__ == '__main__':
    unittest.main()
