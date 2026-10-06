import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from trade_log_dashboard.server import DashboardHandler, DashboardServer
from trade_log_dashboard.storage import load_storage, close_logging


class ResearchHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        storage = load_storage(user_root=Path(self.temp.name) / 'user', project_root=self.temp.name)
        self.server = DashboardServer(('127.0.0.1', 0), DashboardHandler, storage=storage)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.server.runner.close()
        close_logging(self.server.storage)
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_assets_and_runs(self):
        for path in ('/', '/research.js', '/research.css'):
            self.assertEqual(self.request('GET', path)[0], 200)
        status, body = self.request('GET', '/api/research/runs')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'runs': []})

    def test_mutations_require_local_header(self):
        self.assertEqual(self.request('POST', '/api/research/runs', '{}')[0], 403)

    def test_cross_origin_rejected(self):
        self.assertEqual(self.request('GET', '/api/research/runs', headers={'Origin': 'https://example.org'})[0], 403)

    def test_malformed_and_unknown_routes(self):
        headers = {'X-Local-Runner': '1'}
        self.assertEqual(self.request('POST', '/api/research/runs', '[]', headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/research/runs', '{', headers)[0], 400)
        self.assertEqual(self.request('GET', '/api/research/runs/../../secrets')[0], 404)

    def test_connection_does_not_start_model(self):
        with patch('trade_log_dashboard.server.connection_status', return_value={'ready': True, 'message': 'Test connection'}):
            status, body = self.request('GET', '/api/research/connection')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['ready'])

    def test_research_blocks_backtest(self):
        with patch.object(self.server.research, 'has_running_job', return_value=True):
            self.assertEqual(self.request('POST', '/api/backtests', '{}', {'X-Local-Runner': '1'})[0], 409)


if __name__ == '__main__':
    unittest.main()
