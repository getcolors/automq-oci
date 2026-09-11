#!/usr/bin/env python3
"""Write or verify a separate 50-record topic across AutoMQ converges."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid


def command(args, *, data=None, timeout=120):
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        # Commands and stderr may contain authentication details. Keep failures terse.
        raise RuntimeError('command failed with exit ' + str(result.returncode))
    return result.stdout


def remote(profile, script):
    return command(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                    profile + '-0', script])


def records(run):
    return ''.join(f'continuity-{run}-{number:04d}\n' for number in range(1, 51)).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['write', 'verify'])
    parser.add_argument('--manifest', type=Path, required=True,
                        help='Non-secret record manifest to create or read')
    parser.add_argument('--profile', default='automq-gcloud')
    args = parser.parse_args()
    if args.action == 'write':
        if args.manifest.exists():
            raise RuntimeError('manifest already exists; use verify or a new path')
        run = uuid.uuid4().hex
        manifest = {'profile': args.profile, 'topic': 'colors-continuity-' + run,
                    'run': run, 'records': 50, 'sha256': digest(records(run))}
    else:
        manifest = json.loads(args.manifest.read_text())
        if manifest['profile'] != args.profile or manifest['records'] != 50:
            raise RuntimeError('manifest profile or record count mismatch')
    expected = records(manifest['run'])
    if digest(expected) != manifest['sha256']:
        raise RuntimeError('manifest checksum mismatch')
    credentials = {}
    for line in remote(args.profile, 'sudo /usr/local/bin/automq-credential').decode().splitlines():
        key, separator, value = line.partition(':')
        if separator:
            credentials[key] = value.strip()
    for key in ('bootstrap', 'principal', 'password'):
        if not credentials.get(key) or '\n' in credentials[key] or '\r' in credentials[key]:
            raise RuntimeError('missing or invalid client credential field')
    with tempfile.TemporaryDirectory(prefix='automq-continuity-') as directory:
        ca = Path(directory) / 'ca.crt'
        ca.write_bytes(remote(args.profile, 'sudo cat /etc/automq/ca/ca.crt'))
        config = Path(directory) / 'client.conf'
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('\n'.join([
                'security.protocol=SASL_SSL', 'sasl.mechanism=SCRAM-SHA-512',
                'sasl.username=' + credentials['principal'],
                'sasl.password=' + credentials['password'],
                'ssl.ca.location=' + str(ca), 'enable.ssl.certificate.verification=true',
                'ssl.endpoint.identification.algorithm=https', 'message.timeout.ms=30000', '']))
        if args.action == 'write':
            # Create only this unique probe topic using the node's private admin path.
            create = '''import subprocess
from pathlib import Path
props = dict(line.split('=', 1) for line in Path('/etc/automq/server.properties').read_text().splitlines() if '=' in line and not line.startswith('#'))
internal = next(item.split('://', 1)[1] for item in props['listeners'].split(',') if item.startswith('INTERNAL://'))
subprocess.run(['docker', 'exec', '-e', 'KAFKA_HEAP_OPTS=-Xmx256m', 'automq', '/opt/automq/kafka/bin/kafka-topics.sh', '--bootstrap-server', internal, '--command-config', '/etc/automq/admin.properties', '--create', '--topic', TOPIC, '--partitions', '1', '--replication-factor', '1'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''.replace('TOPIC', repr(manifest['topic']))
            remote(args.profile, 'sudo python3 -c ' + shlex.quote(create))
        client = ['kcat', '-F', str(config), '-b', credentials['bootstrap'],
                  '-t', manifest['topic'], '-p', '0']
        if args.action == 'write':
            command(client + ['-P'], data=expected)
        received = command(client + ['-C', '-o', 'beginning', '-e', '-q'])
        if received != expected:
            raise RuntimeError('continuity records differ from the exact expected set')
    if args.action == 'write':
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open('x') as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write('\n')
    print(json.dumps({**manifest, 'action': args.action, 'passed': True}, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as exc:
        # Do not expose subprocess arguments or output in exception messages.
        print(json.dumps({'passed': False, 'error': type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
