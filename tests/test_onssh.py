"""End-to-end tests: browser WebSocket -> onssh -> an in-process SSH server on 127.0.0.1."""
import json
import logging
import os
import socket
import tempfile
import threading

import paramiko
import tornado.testing
import tornado.websocket
from tornado.httpclient import HTTPClientError, HTTPRequest

from onssh.app import make_app
from onssh.ssh import Allowlist, KnownHosts, SessionError, check_target

USER, PASSWORD = 'alice', 'correct horse battery staple'
HOST_KEY = paramiko.RSAKey.generate(2048)
OTHER_KEY = paramiko.RSAKey.generate(2048)


class FakeServer(paramiko.ServerInterface):

    def __init__(self, record):
        self.record = record

    def get_allowed_auths(self, username):
        return 'password'

    def check_auth_password(self, username, password):
        ok = username == USER and password == PASSWORD
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == 'session':
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        self.record['pty'] = (term, width, height)
        return True

    def check_channel_shell_request(self, channel):
        return True

    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
        self.record['resize'] = (width, height)
        return True


class SSHServer:
    """Echo shell: prints a prompt, echoes each line, closes on 'exit'."""

    def __init__(self, key=HOST_KEY):
        self.key = key
        self.record = {}
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        t = paramiko.Transport(conn)
        t.add_server_key(self.key)
        try:
            t.start_server(server=FakeServer(self.record))
        except (paramiko.SSHException, EOFError):
            return
        chan = t.accept(10)
        if chan is None:
            t.close()
            return
        chan.sendall(b'welcome \xe4\xbd\xa0\xe5\xa5\xbd\r\n$ ')
        buf = b''
        while True:
            data = chan.recv(1024)
            if not data:
                break
            buf += data
            while b'\r' in buf:
                line, buf = buf.split(b'\r', 1)
                if line == b'exit':
                    chan.send_exit_status(0)
                    chan.close()
                    t.close()
                    return
                chan.sendall(b'echo:' + line + b'\r\n$ ')

    def close(self):
        self.sock.close()


class OnsshTest(tornado.testing.AsyncHTTPTestCase):

    allow = ()
    strict = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.known = os.path.join(self.tmp.name, 'sub', 'known_hosts')
        self.server = SSHServer()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.server.close()
        self.tmp.cleanup()

    def get_app(self):
        return make_app(KnownHosts(self.known, strict=self.strict), Allowlist(self.allow))

    async def open(self, origin=None):
        url = f'ws://127.0.0.1:{self.get_http_port()}/ws'
        headers = {'Origin': origin} if origin else {}
        return await tornado.websocket.websocket_connect(HTTPRequest(url, headers=headers))

    async def login(self, password=PASSWORD, port=None, **extra):
        ws = await self.open()
        msg = dict(type='connect', host='127.0.0.1', port=port or self.server.port,
                   username=USER, password=password, cols=100, rows=30)
        msg.update(extra)
        ws.write_message(json.dumps(msg))
        return ws

    async def read_until(self, ws, text):
        """Collect messages until terminal output contains text; returns (output, messages)."""
        out, msgs = '', []
        while text not in out:
            raw = await ws.read_message()
            if raw is None:
                self.fail(f'closed before {text!r}; got {msgs}')
            msg = json.loads(raw)
            msgs.append(msg)
            if msg['type'] == 'data':
                out += msg['data']
            elif msg['type'] == 'error':
                self.fail(f'error before {text!r}: {msg}')
        return out, msgs

    async def read_error(self, ws):
        msgs = []
        while True:
            raw = await ws.read_message()
            if raw is None:
                break
            msgs.append(json.loads(raw))
        errors = [m['message'] for m in msgs if m['type'] == 'error']
        self.assertEqual(len(errors), 1, msgs)
        return errors[0]


class SessionTest(OnsshTest):

    def test_index_is_self_contained(self):
        r = self.fetch('/')
        self.assertEqual(r.code, 200)
        body = r.body.decode()
        self.assertNotIn('http://', body)
        self.assertNotIn('https://', body)
        self.assertIn("default-src 'self'", r.headers['Content-Security-Policy'])
        self.assertEqual(self.fetch('/static/vendor/xterm/xterm.js').code, 200)

    @tornado.testing.gen_test
    async def test_login_echo_and_utf8(self):
        ws = await self.login()
        out, msgs = await self.read_until(ws, '$ ')
        self.assertIn('welcome 你好', out)
        self.assertEqual(self.server.record['pty'], (b'xterm-256color', 100, 30))
        ws.write_message(json.dumps({'type': 'data', 'data': 'hello\r'}))
        out, _ = await self.read_until(ws, 'echo:hello')
        ws.write_message(json.dumps({'type': 'data', 'data': 'exit\r'}))
        while await ws.read_message() is not None:
            pass

    @tornado.testing.gen_test
    async def test_first_use_records_host_key(self):
        ws = await self.login()
        _, msgs = await self.read_until(ws, '$ ')
        notes = [m['message'] for m in msgs if m['type'] == 'status']
        self.assertEqual(len(notes), 1)
        self.assertIn('Permanently added [127.0.0.1]:%d' % self.server.port, notes[0])
        self.assertIn('SHA256:', notes[0])
        self.assertEqual(os.stat(self.known).st_mode & 0o777, 0o600)
        ws.close()

        ws = await self.login()
        _, msgs = await self.read_until(ws, '$ ')
        self.assertFalse([m for m in msgs if m['type'] == 'status'])
        ws.close()

    @tornado.testing.gen_test
    async def test_changed_host_key_is_refused(self):
        keys = paramiko.HostKeys()
        keys.add('[127.0.0.1]:%d' % self.server.port, OTHER_KEY.get_name(), OTHER_KEY)
        os.makedirs(os.path.dirname(self.known))
        keys.save(self.known)
        ws = await self.login()
        self.assertIn('host key for 127.0.0.1 has changed', await self.read_error(ws))

    @tornado.testing.gen_test
    async def test_wrong_password_is_not_echoed_or_logged(self):
        with self.assertLogs('onssh', logging.INFO) as logs:
            ws = await self.login(password='hunter2-wrong')
            err = await self.read_error(ws)
        self.assertEqual(err, 'authentication failed')
        self.assertNotIn('hunter2', '\n'.join(logs.output))

    @tornado.testing.gen_test
    async def test_resize(self):
        ws = await self.login()
        await self.read_until(ws, '$ ')
        ws.write_message(json.dumps({'type': 'resize', 'cols': 132, 'rows': 43}))
        ws.write_message(json.dumps({'type': 'data', 'data': 'sync\r'}))
        await self.read_until(ws, 'echo:sync')
        self.assertEqual(self.server.record['resize'], (132, 43))
        ws.close()

    @tornado.testing.gen_test
    async def test_connection_refused(self):
        closed = socket.socket()
        closed.bind(('127.0.0.1', 0))
        port = closed.getsockname()[1]
        closed.close()
        ws = await self.login(port=port)
        self.assertIn('could not connect to 127.0.0.1:%d' % port, await self.read_error(ws))

    @tornado.testing.gen_test
    async def test_bad_input(self):
        ws = await self.login(port=70000)
        self.assertEqual(await self.read_error(ws), 'invalid port')
        ws = await self.open()
        ws.write_message('not json')
        self.assertEqual(await self.read_error(ws), 'bad message')

    @tornado.testing.gen_test
    async def test_cross_origin_websocket_is_rejected(self):
        with self.assertRaises(HTTPClientError) as cm:
            await self.open(origin='https://evil.example')
        self.assertEqual(cm.exception.code, 403)


class StrictTest(OnsshTest):
    strict = True

    @tornado.testing.gen_test
    async def test_unknown_host_key_is_refused(self):
        ws = await self.login()
        self.assertIn('unknown host key', await self.read_error(ws))
        self.assertFalse(os.path.exists(self.known))


class AllowlistTest(OnsshTest):
    allow = ('10.0.0.0/8', 'db.example.com')

    @tornado.testing.gen_test
    async def test_target_outside_allowlist_is_refused(self):
        ws = await self.login()
        self.assertEqual(await self.read_error(ws), '127.0.0.1 is not in the allowed targets')
        self.assertNotIn('pty', self.server.record)


class UnitTest(tornado.testing.AsyncTestCase):

    def test_allowlist(self):
        a = Allowlist(['10.0.0.0/8', '*.example.com', 'db1.corp', '::1/128'])
        self.assertTrue(a.allows('anything', '10.1.2.3'))
        self.assertTrue(a.allows('a.b.example.com', '192.0.2.1'))
        self.assertFalse(a.allows('example.com.evil', '192.0.2.1'))
        self.assertTrue(a.allows('DB1.corp.', '192.0.2.1'))
        self.assertTrue(a.allows('localhost', '::1'))
        self.assertFalse(a.allows('localhost', '127.0.0.1'))
        self.assertTrue(Allowlist([]).allows('x', '192.0.2.1'))

    def test_check_target(self):
        self.assertEqual(check_target('Host-1.example.com', '22', 'u'), ('Host-1.example.com', 22, 'u'))
        self.assertEqual(check_target('[2001:db8::1]', 2222, 'u'), ('2001:db8::1', 2222, 'u'))
        for host, port, user in [('a b', 22, 'u'), ('-x', 22, 'u'), ('h', 0, 'u'),
                                 ('h', 'x', 'u'), ('h', 22, ''), ('h', 22, 'u\n')]:
            with self.assertRaises(SessionError):
                check_target(host, port, user)
