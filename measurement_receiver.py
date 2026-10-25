"""Loopback receiver for authenticated Node monitor events, not a public data API."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start_receiver(store, port=8766):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 1_000_000:
                    raise ValueError('Invalid payload size.')
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                data = json.loads(self.rfile.read(length))
                if self.path == '/start':
                    result = {'run_id': store.start(token, data.get('run_id'))}
                elif self.path == '/heartbeat':
                    store.heartbeat(token, data['run_id'])
                    result = {'ok': True}
                elif self.path == '/metrics':
                    store.ingest(token, data['run_id'], data['metrics'], data['position'], data['levels'], data.get('timestampUs'))
                    result = {'ok': True}
                elif self.path == '/event':
                    store.event(token, data['run_id'], data['kind'], data.get('position', 0), data.get('detail'))
                    result = {'ok': True}
                else:
                    self.reply(404, {'error': 'Not found'}); return
                self.reply(200, result)
            except PermissionError:
                self.reply(403, {'error': 'Session authorisation failed.'})
            except (ValueError, KeyError, TypeError):
                self.reply(400, {'error': 'Invalid measurement payload.'})
            except Exception:
                self.reply(500, {'error': 'Could not save measurements.'})

        def reply(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
