"""Per-turn loopback Responses bridge: reserve and settle every upstream request."""
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from .accounting import begin_run, settle_run
from .store import LeaseLost, dumps


def response_usage(value):
    if not isinstance(value, dict): return {}
    usage = dict(value)
    if 'cached_input_tokens' not in usage and 'input_tokens_details' in usage:
        details = usage['input_tokens_details']
        if not isinstance(details, dict): return {}
        if 'cached_tokens' in details: usage['cached_input_tokens'] = details['cached_tokens']
    return usage


class ModelProxy:
    def __init__(self, ctx, config, key):
        self.ctx, self.config, self.key = ctx, config, key
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.active = True
        self.responses = set()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, code, message):
                value = dumps({'error': {'message': message}}).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(value)))
                self.end_headers()
                try: self.wfile.write(value)
                except (BrokenPipeError, ConnectionError, OSError): pass

            def do_POST(self):
                self.connection.settimeout(10)
                if self.path != '/v1/responses':
                    self.reply(404, 'Unsupported model endpoint'); return
                if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + proxy.token).encode()):
                    self.reply(401, 'Invalid turn token'); return
                with proxy.lock:
                    if not proxy.active:
                        self.reply(410, 'Management turn ended'); return
                length = self.headers.get('Content-Length', '')
                if self.headers.get('Transfer-Encoding') or not length.isascii() or not length.isdecimal():
                    self.reply(400, 'Expected a bounded request'); return
                if not 0 < int(length) <= proxy.config.max_prompt_bytes:
                    self.reply(413, 'Model input limit exceeded'); return
                try:
                    body = self.rfile.read(int(length))
                    if len(body) != int(length): raise ValueError('Truncated request')
                    payload = json.loads(body)
                    if not isinstance(payload, dict) or not isinstance(payload.get('stream', False), bool):
                        raise ValueError('Invalid request')
                except (ValueError, OSError):
                    self.reply(400, 'Invalid model request'); return
                payload['model'] = proxy.config.model
                payload['max_output_tokens'] = proxy.config.max_output_tokens
                proxy.forward(self, payload)

            def do_GET(self): self.reply(405, 'Only Responses POST is supported')
            def do_PUT(self): self.reply(405, 'Only Responses POST is supported')
            def do_OPTIONS(self): self.reply(405, 'Only Responses POST is supported')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = 'http://127.0.0.1:{}/v1'.format(self.server.server_port)

    def sanitized(self, value):
        if isinstance(value, str):
            return value.replace(self.key, '[redacted]').replace(self.token, '[redacted]')
        if isinstance(value, list): return [self.sanitized(item) for item in value]
        if isinstance(value, dict): return {key: self.sanitized(item) for key, item in value.items()}
        return value

    def forward(self, handler, payload):
        from fastapi import HTTPException
        from .access import authorize_execution
        run_id = None
        settled = False
        sent_headers = False
        response = None
        def unknown():
            nonlocal settled
            if run_id and not settled:
                settle_run(run_id, {}, 'failed')
                settled = True
        try:
            # Each provider request, including CLI built-in tool continuations,
            # must re-prove the live administrator/session/task binding.
            authorize_execution(self.ctx.job)
            run_id = begin_run(self.ctx.job, self.config, self.config.max_prompt_bytes,
                               require_balance=True)
            with self.lock:
                if not self.active: raise LeaseLost('Management turn ended')
            with requests.Session() as client:
                client.trust_env = False
                response = client.post(self.config.base_url.rstrip('/') + '/responses', json=payload,
                    headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'},
                    stream=True, timeout=(10, min(45, self.config.timeout_seconds)), allow_redirects=False)
                with self.lock:
                    if not self.active:
                        response.close(); raise LeaseLost('Management turn ended')
                    self.responses.add(response)
                if response.status_code != 200:
                    unknown()
                    handler.reply(502, 'Model provider request failed'); return
                streaming = payload.get('stream', False)
                if streaming:
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'text/event-stream')
                    handler.send_header('Cache-Control', 'no-store')
                    handler.end_headers()
                    sent_headers = True
                    block, total = [], 0
                    for line in response.iter_lines(chunk_size=4096):
                        line = line.rstrip(b'\r')
                        total += len(line) + 1
                        if total > 8 * 1024 * 1024: raise ValueError('Model output limit exceeded')
                        if line:
                            block.append(line)
                            if sum(map(len, block)) > 2 * 1024 * 1024:
                                raise ValueError('Model event limit exceeded')
                            continue
                        data = b'\n'.join(item[5:].lstrip() for item in block if item.startswith(b'data:'))
                        if data and data != b'[DONE]':
                            value = json.loads(data)
                            if value.get('type') in ('response.completed', 'response.failed') and not settled:
                                result = value.get('response') or {}
                                settle_run(run_id, response_usage(result.get('usage')),
                                           'success' if value['type'] == 'response.completed' else 'failed')
                                settled = True
                            block = [item for item in block if not item.startswith(b'data:')] + [
                                b'data: ' + dumps(self.sanitized(value)).encode()]
                        # Settlement happens before completion reaches the CLI,
                        # so its next request sees missing usage or debited points.
                        handler.wfile.write(b'\n'.join(block) + b'\n\n')
                        handler.wfile.flush()
                        block = []
                    if block: raise ValueError('Truncated model event')
                else:
                    raw = bytearray()
                    for chunk in response.iter_content(65536):
                        raw.extend(chunk)
                        if len(raw) > 8 * 1024 * 1024: raise ValueError('Model output limit exceeded')
                    result = json.loads(raw)
                    settle_run(run_id, response_usage(result.get('usage')),
                               'success' if result.get('status', 'completed') == 'completed' else 'failed')
                    settled = True
                    raw = dumps(self.sanitized(result)).encode()
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'application/json')
                    handler.send_header('Content-Length', str(len(raw)))
                    handler.end_headers()
                    sent_headers = True
                    handler.wfile.write(raw)
        except HTTPException as error:
            unknown()
            if not sent_headers: handler.reply(error.status_code, 'Model request admission refused')
        except ValueError:
            unknown()
            if not sent_headers: handler.reply(402 if run_id is None else 502, 'Model request budget or data refused')
        except LeaseLost:
            unknown()
            if not sent_headers: handler.reply(403, 'Management turn authorization ended')
        except Exception:
            unknown()
            if not sent_headers: handler.reply(502, 'Model provider connection failed')
        finally:
            unknown()
            if response is not None:
                with self.lock: self.responses.discard(response)
                response.close()

    def close(self):
        with self.lock:
            self.active = False
            responses = list(self.responses)
        for response in responses: response.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
