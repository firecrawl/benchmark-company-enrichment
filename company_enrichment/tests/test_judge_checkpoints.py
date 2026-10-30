"""Completed vote rows survive an owned process being killed during the next item.

The five original judge names use a fixture-only gateway route so every request
uses the real stdlib HTTP adapter. This does not exercise Anthropic's SDK or the
production route configuration, and makes no model-quality measurement.
"""
import json
import os
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from company_enrichment.scorer import industry

ROOT = Path(__file__).resolve().parents[2]
CHILD = r'''
import json,socket,sys,urllib.parse,urllib.request
from company_enrichment.scorer import industry
origin,out = sys.argv[1:]
class LocalGateway(urllib.request.HTTPSHandler):
    def https_open(self, request):
        assert urllib.parse.urlsplit(request.full_url).hostname == 'ai-gateway.vercel.sh'
        local = urllib.request.Request(origin + '/v1/chat/completions', data=request.data,
            headers=dict(request.header_items()), method=request.get_method())
        return self.parent.open(local)
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalGateway()))
connect=socket.socket.connect
socket.socket.connect=lambda self,address: connect(self,address) if address[0] in ('127.0.0.1','::1') else (_ for _ in ()).throw(RuntimeError('External socket refused'))
# Route only the test's five original names through the owned HTTP fixture.
industry.JUDGES = {name: ('gateway', model) for name, (_, model) in industry.JUDGES.items()}
items = [{'id': f'fixture-{i}||fixture sector', 'company_id': f'fixture-{i}',
          'company': 'Synthetic Company', 'description': 'Synthetic company description.',
          'label': 'Fixture Sector'} for i in (1,2)]
industry.judge(items, out)
'''


class Gateway:
    def __init__(self, hold_second_item):
        self.calls = []
        self.held = threading.Event()
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append(body)
                if hold_second_item and len(owner.calls) > len(industry.JUDGES):
                    owner.held.set()
                    owner.release.wait(20)
                payload = {'choices': [{'message': {'content': json.dumps({
                    'score': '1', 'rationale': 'Synthetic fixture judgment.'})}}]}
                raw = json.dumps(payload).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The owned child was killed while this request was held.

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def command(gateway, path):
    return [sys.executable, '-u', '-c', CHILD, gateway.origin, str(path)]


def environment():
    return {'PATH': os.defpath, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1',
            'ANTHROPIC_API_KEY': 'synthetic-local', 'AI_GATEWAY_API_KEY': 'synthetic-local'}


@pytest.mark.parametrize('existing', [False, True])
def test_completed_votes_survive_process_interruption(tmp_path, existing):
    path = tmp_path / 'votes.jsonl'
    seed = json.dumps({'company_id': 'earlier', 'label': 'fixture sector',
                       'votes': {name: 0.5 for name in industry.JUDGES}}) + '\n'
    if existing:
        path.write_text(seed)
    gateway = Gateway(hold_second_item=True)
    process = subprocess.Popen(command(gateway, path), cwd=ROOT, env=environment(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
    reader.start()
    try:
        line = lines.get(timeout=20)
        assert 'judged fixture-1||fixture sector' in line, line
        assert gateway.held.wait(10), 'The next item must still be in flight when interrupted'
        process.kill()
        process.wait(timeout=5)
        raw = path.read_text()
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        assert [row['company_id'] for row in rows] == (['earlier'] if existing else []) + ['fixture-1']
        assert rows[-1]['votes'] == {name: 1.0 for name in industry.JUDGES}
        assert rows[-1]['prompt_sha256'] == industry.PROMPT_SHA
        if existing:
            assert raw.startswith(seed)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()
        gateway.close()


def test_ordinary_batch_retains_all_five_votes_and_append_format(tmp_path):
    path = tmp_path / 'votes.jsonl'
    gateway = Gateway(hold_second_item=False)
    try:
        result = subprocess.run(command(gateway, path), cwd=ROOT, env=environment(),
                                capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stderr
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row['company_id'] for row in rows] == ['fixture-1', 'fixture-2']
        assert all(row['votes'] == {name: 1.0 for name in industry.JUDGES} for row in rows)
        assert all(row['prompt_sha256'] == industry.PROMPT_SHA for row in rows)
        assert len(gateway.calls) == 2 * len(industry.JUDGES)
        assert industry.load_grades([path]) == {'fixture-1||fixture sector': 1.0,
                                               'fixture-2||fixture sector': 1.0}
    finally:
        gateway.close()
