"""Tornado application: the login page and the WebSocket that carries the terminal.

WebSocket messages are JSON objects in both directions:
  browser -> server  {"type": "connect", "host", "port", "username", "password", "cols", "rows"}
                     {"type": "data", "data": "<keystrokes>"}
                     {"type": "resize", "cols": 120, "rows": 40}
  server -> browser  {"type": "data", "data": "<terminal output>"}
                     {"type": "status", "message": "..."}
                     {"type": "error", "message": "..."}   (the server closes afterwards)
"""
import json
import logging
import os

import tornado.ioloop
import tornado.web
import tornado.websocket

from . import ssh

log = logging.getLogger('onssh')
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_MESSAGE = 1 << 20


def _size(msg, key, default, limit):
    try:
        value = int(msg.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, limit))


class IndexHandler(tornado.web.RequestHandler):

    def set_default_headers(self):
        self.set_header('Content-Security-Policy',
                        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                        "frame-ancestors 'none'")
        self.set_header('X-Content-Type-Options', 'nosniff')
        self.set_header('Referrer-Policy', 'no-referrer')

    def get(self):
        self.render('index.html')


class TerminalHandler(tornado.websocket.WebSocketHandler):
    # tornado's default check_origin rejects cross-site pages, which is what
    # keeps another website from opening SSH sessions through a user's browser

    def initialize(self, allowlist, known_hosts):
        self.allowlist, self.known_hosts = allowlist, known_hosts
        self.shell = None
        self.connecting = False

    def send(self, kind, **fields):
        try:
            self.write_message(json.dumps(dict(type=kind, **fields)))
        except tornado.websocket.WebSocketClosedError:
            pass

    def fail(self, message):
        self.send('error', message=message)
        self.close()

    async def on_message(self, message):
        try:
            msg = json.loads(message)
            kind = msg['type']
        except (ValueError, TypeError, KeyError):
            self.fail('bad message')
            return
        if kind == 'connect' and self.shell is None and not self.connecting:
            await self.start(msg)
        elif kind == 'data' and self.shell and isinstance(msg.get('data'), str):
            self.shell.write(msg['data'])
        elif kind == 'resize' and self.shell:
            self.shell.resize(_size(msg, 'cols', 80, 1000), _size(msg, 'rows', 24, 1000))

    async def start(self, msg):
        self.connecting = True
        peer = self.request.remote_ip
        try:
            host, port, username = ssh.check_target(msg.get('host'), msg.get('port', 22),
                                                    msg.get('username'))
        except ssh.SessionError as e:
            self.fail(str(e))
            return
        password = msg.get('password')
        if not isinstance(password, str):
            password = ''
        loop = tornado.ioloop.IOLoop.current()
        try:
            client, chan, note = await loop.run_in_executor(
                None, lambda: ssh.connect(host, port, username, password,
                                          allowlist=self.allowlist, known_hosts=self.known_hosts,
                                          cols=_size(msg, 'cols', 80, 1000),
                                          rows=_size(msg, 'rows', 24, 1000)))
        except ssh.SessionError as e:
            log.info('%s: %s@%s:%d failed: %s', peer, username, host, port, e)
            self.fail(str(e))
            return
        if self.ws_connection is None:      # browser went away while we were logging in
            chan.close()
            client.close()
            return
        log.info('%s: %s@%s:%d connected', peer, username, host, port)
        if note:
            self.send('status', message=note)
        self.shell = ssh.Shell(loop, client, chan,
                               on_data=lambda text: self.send('data', data=text),
                               on_close=self.close)

    def on_close(self):
        if self.shell:
            self.shell.close()


def make_app(known_hosts, allowlist=None, debug=False):
    return tornado.web.Application(
        [
            (r'/', IndexHandler),
            (r'/ws', TerminalHandler, dict(allowlist=allowlist or ssh.Allowlist(),
                                           known_hosts=known_hosts)),
        ],
        template_path=os.path.join(HERE, 'templates'),
        static_path=os.path.join(HERE, 'static'),
        websocket_max_message_size=MAX_MESSAGE,
        debug=debug,
    )
