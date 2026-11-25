#!/usr/bin/env python3
"""Request an existing-instance connector pairing; approval remains in Dash."""
import argparse
import os
from pathlib import Path


def command(args):
    if not Path(args.credential_store).is_absolute() or not Path(args.rbq).is_absolute():
        raise ValueError('credential store and RBQ executable must be absolute paths')
    if not 0 <= args.timeout <= 600:
        raise ValueError('timeout must be between 0 and 600 seconds')
    return [args.rbq, 'dash', '--credential-store', args.credential_store, '--seal', args.seal,
            'ensure', '--dash', args.dash, '--label', args.label, '--scope', 'connector',
            '--timeout', str(args.timeout)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seal', required=True, choices=('none', 'secret-service'),
                        help='Explicit at-rest choice: none means owner-only plaintext files, not encryption')
    parser.add_argument('--credential-store', default='/var/lib/rebekah/gateway/rbq-credentials')
    parser.add_argument('--dash', default=os.environ.get('REBEKAH_DASH_URL', 'https://dash.ragbaz.cc'))
    parser.add_argument('--label', default=os.environ.get('HOSTNAME', 'Rebekah'))
    parser.add_argument('--timeout', type=int, default=0)
    parser.add_argument('--rbq', default='/usr/local/bin/rbq')
    args = parser.parse_args()
    try:
        argv = command(args)
    except ValueError as error:
        parser.error(str(error))
    # The image's gateway owns this store. Other services must never read it.
    # Explicit root invocation drops the same way as the supervisor; no extra
    # capability is added. Non-root invocations retain only their own authority.
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(10005)
        os.setuid(10005)
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'XDG_RUNTIME_DIR',
        'DBUS_SESSION_BUS_ADDRESS', 'SSL_CERT_FILE', 'NIX_SSL_CERT_FILE') if key in os.environ}
    os.execve(args.rbq, argv, env)


if __name__ == '__main__':
    main()
