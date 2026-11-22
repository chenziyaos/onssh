"""SSH side of a browser session: target checks, host keys, and the shell channel."""
import base64
import codecs
import hashlib
import ipaddress
import logging
import os
import re
import socket
import threading

import paramiko

log = logging.getLogger('onssh')

CONNECT_TIMEOUT = 10
READ_SIZE = 32768
_LABEL = r'[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
_HOSTNAME = re.compile(rf'^(?=.{{1,253}}$){_LABEL}(\.{_LABEL})*\.?$')


class SessionError(Exception):
    """An error whose message is safe to show to the browser."""


def fingerprint(key):
    digest = hashlib.sha256(key.asbytes()).digest()
    return 'SHA256:' + base64.b64encode(digest).decode().rstrip('=')


def check_target(host, port, username):
    """Validate the login form; returns (host, port, username)."""
    host = str(host or '').strip()
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME.match(host):
            raise SessionError('invalid host') from None
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise SessionError('invalid port') from None
    if not 0 < port < 65536:
        raise SessionError('invalid port')
    username = str(username or '')
    if not username or len(username) > 256 or any(ord(c) < 32 for c in username):
        raise SessionError('invalid username')
    return host, port, username


class Allowlist:
    """Targets the gateway may connect to: CIDRs match resolved addresses,
    names match the host as typed ("*.example.com" matches subdomains).
    An empty list allows everything."""

    def __init__(self, entries=()):
        self.networks, self.names = [], []
        for entry in entries:
            entry = entry.strip().lower()
            if not entry:
                continue
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self.names.append(entry)

    def __bool__(self):
        return bool(self.networks or self.names)

    def allows(self, host, addr):
        if not self:
            return True
        host = host.lower().rstrip('.')
        for name in self.names:
            if host == name or (name.startswith('*.') and host.endswith(name[1:])):
                return True
        ip = ipaddress.ip_address(addr.split('%', 1)[0])
        return any(ip in net for net in self.networks)


class KnownHosts:
    """A known_hosts file shared by all sessions, with trust on first use."""

    def __init__(self, path, strict=False):
        self.path = path
        self.strict = strict
        self.lock = threading.Lock()

    def load_into(self, client):
        with self.lock:
            if os.path.exists(self.path):
                client.load_host_keys(self.path)

    def policy(self):
        return _Policy(self)

    def add(self, hostname, key):
        with self.lock:
            keys = paramiko.HostKeys()
            if os.path.exists(self.path):
                keys.load(self.path)
            keys.add(hostname, key.get_name(), key)
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, mode=0o700, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.close(fd)
            keys.save(self.path)


class _Policy(paramiko.MissingHostKeyPolicy):

    def __init__(self, known):
        self.known = known
        self.added = None

    def missing_host_key(self, client, hostname, key):
        if self.known.strict:
            raise SessionError(f'unknown host key for {hostname} ({key.get_name()} {fingerprint(key)}); '
                               f'add it to {self.known.path} first')
        self.known.add(hostname, key)
        self.added = f'{hostname} ({key.get_name()} {fingerprint(key)})'


def _open_socket(host, port, allowlist):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise SessionError(f'could not resolve {host}') from None
    infos = [i for i in infos if allowlist.allows(host, i[4][0])]
    if not infos:
        raise SessionError(f'{host} is not in the allowed targets')
    err = None
    for family, kind, proto, _, addr in infos:
        sock = socket.socket(family, kind, proto)
        sock.settimeout(CONNECT_TIMEOUT)
        try:
            sock.connect(addr)
            return sock
        except OSError as e:
            sock.close()
            err = e
    raise SessionError(f'could not connect to {host}:{port}: {err.strerror or err}')


def connect(host, port, username, password, *, allowlist, known_hosts, cols=80, rows=24):
    """Blocking: log in and open an interactive shell.
    Returns (client, channel, note) where note is set when a host key was added."""
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts.load_into(client)
    policy = known_hosts.policy()
    client.set_missing_host_key_policy(policy)
    sock = _open_socket(host, port, allowlist)
    try:
        # never offer the gateway's own keys or agent to an arbitrary server
        client.connect(host, port, username=username, password=password, sock=sock,
                       timeout=CONNECT_TIMEOUT, banner_timeout=CONNECT_TIMEOUT,
                       auth_timeout=CONNECT_TIMEOUT, look_for_keys=False, allow_agent=False)
        chan = client.invoke_shell(term='xterm-256color', width=cols, height=rows)
    except SessionError:
        client.close()
        raise
    except paramiko.BadHostKeyException as e:
        client.close()
        raise SessionError(f'host key for {host} has changed (now {e.key.get_name()} '
                           f'{fingerprint(e.key)}); if the server was reinstalled, remove the old '
                           f'entry from {known_hosts.path}') from None
    except paramiko.AuthenticationException:
        client.close()
        raise SessionError('authentication failed') from None
    except (paramiko.SSHException, OSError, EOFError) as e:
        client.close()
        raise SessionError(f'ssh error: {e}') from None
    note = None
    if policy.added:
        note = f'Permanently added {policy.added} to {known_hosts.path}'
    return client, chan, note


class Shell:
    """Pumps an SSH channel to callbacks on the IOLoop thread."""

    def __init__(self, ioloop, client, chan, on_data, on_close):
        self.ioloop, self.client, self.chan = ioloop, client, chan
        self.on_data, self.on_close = on_data, on_close
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.pending = b''
        self.closed = False
        chan.settimeout(0.0)
        ioloop.add_handler(chan.fileno(), self._readable, ioloop.READ)

    def _readable(self, fd, events):
        while not self.closed:
            try:
                data = self.chan.recv(READ_SIZE)
            except socket.timeout:
                return
            if not data:
                self.close()
                return
            self.on_data(self.decoder.decode(data))

    def write(self, text):
        if self.closed:
            return
        self.pending += text.encode('utf-8')
        self._flush()

    def _flush(self):
        while self.pending and not self.closed:
            try:
                n = self.chan.send(self.pending)
            except socket.timeout:      # remote window full, retry shortly
                self.ioloop.call_later(0.05, self._flush)
                return
            except OSError:
                self.close()
                return
            self.pending = self.pending[n:]

    def resize(self, cols, rows):
        if not self.closed:
            self.chan.resize_pty(width=cols, height=rows)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.ioloop.remove_handler(self.chan.fileno())
        self.chan.close()
        self.client.close()
        self.on_close()
