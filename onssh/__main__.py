"""python -m onssh [--address 127.0.0.1] [--port 8000] [--allow CIDR|NAME ...]"""
import argparse
import asyncio
import ipaddress
import logging
import os

from . import __version__
from .app import make_app
from .ssh import Allowlist, KnownHosts

log = logging.getLogger('onssh')


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog='onssh', description='SSH terminal in the browser.')
    p.add_argument('--address', default='127.0.0.1',
                   help='address to listen on (default: 127.0.0.1)')
    p.add_argument('--port', type=int, default=8000, help='port to listen on (default: 8000)')
    p.add_argument('--allow', action='append', default=[], metavar='CIDR|NAME',
                   help='allowed SSH targets, repeatable or comma separated: 10.0.0.0/8, '
                        'db1.example.com, *.example.com (default: any)')
    p.add_argument('--known-hosts', default=os.path.expanduser('~/.config/onssh/known_hosts'),
                   help='file for trusted host keys (default: %(default)s)')
    p.add_argument('--strict-host-keys', action='store_true',
                   help='refuse hosts whose key is not in known_hosts yet')
    p.add_argument('--debug', action='store_true', help='tornado debug mode (autoreload)')
    p.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    return p.parse_args(argv)


def is_loopback(address):
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return address == 'localhost'


async def serve(args):
    allowlist = Allowlist(e for arg in args.allow for e in arg.split(','))
    known_hosts = KnownHosts(args.known_hosts, strict=args.strict_host_keys)
    app = make_app(known_hosts, allowlist, debug=args.debug)
    # trust X-Forwarded-For / X-Real-IP only from a reverse proxy on the same host
    app.listen(args.port, address=args.address, xheaders=is_loopback(args.address))
    log.info('onssh %s listening on http://%s:%d/', __version__, args.address, args.port)
    if not is_loopback(args.address):
        log.warning('listening on %s: anyone who can reach this port can try SSH logins '
                    'through it; put it behind TLS and authentication', args.address)
        if not allowlist:
            log.warning('no --allow given: any SSH target is reachable through this gateway')
    await asyncio.Event().wait()


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        asyncio.run(serve(parse_args(argv)))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
