"""Use the existing Actions deploy credential with the verified host public key."""
import os
from pathlib import Path


def prepare():
    host, user, key = (os.environ[name].strip() for name in ('SSH_HOST', 'SSH_USER', 'SSH_KEY'))
    port = int(os.environ.get('SSH_PORT') or '22')
    if not host or not user or not key or not 1 <= port <= 65535:
        raise ValueError('Existing SSH_HOST, SSH_USER and SSH_KEY secrets are required')
    if any(c.isspace() for c in host + user) or any(c in host + user for c in '\x00\r\n"'):
        raise ValueError('Invalid SSH destination')
    root = Path.home() / '.ssh'
    root.mkdir(mode=0o700, exist_ok=True)
    identity = root / 'speda-actions'
    identity.write_text(key.replace('\r\n', '\n') + '\n')
    identity.chmod(0o600)
    public = Path('.github/production-host-key.pub').read_text().strip()
    assert public.startswith('ssh-ed25519 ') and len(public.split()) == 2
    known = root / 'speda-known-hosts'
    known.write_text(f'[{host}]:{port} {public}\n{host} {public}\n')
    config = root / 'config'
    config.write_text(f'''Host speda-production
    HostName {host}
    User {user}
    Port {port}
    IdentityFile {identity}
    IdentitiesOnly yes
    BatchMode yes
    StrictHostKeyChecking yes
    UserKnownHostsFile {known}
    ConnectTimeout 20
    ServerAliveInterval 15
    ServerAliveCountMax 8
''')
    config.chmod(0o600)
    print('Existing deploy credential configured; strict host verification enabled.')


if __name__ == '__main__':
    prepare()
