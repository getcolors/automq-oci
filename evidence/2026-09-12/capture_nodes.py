#!/usr/bin/env python3
"""Capture non-secret node identity, credential hashes and TCP boundaries.

Run after convergence finishes. --compare checks that another full converge
kept the same machines, formatted identities, cluster secrets and CA.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import socket
import subprocess
import sys


FACTS = r'''
import hashlib, json, platform, re, subprocess
from pathlib import Path
def properties(path):
    return dict(line.split('=', 1) for line in Path(path).read_text().splitlines()
                if '=' in line and not line.startswith('#'))
def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
chain = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
                   Path('/etc/automq/tls/fullchain.pem').read_text(), re.S)
if len(chain) != 2:
    raise ValueError('expected leaf and private CA in the installed TLS chain')
ca_der = subprocess.run(['openssl', 'x509', '-outform', 'DER'],
                        input=chain[-1].encode(), capture_output=True, check=True).stdout
props = properties('/etc/automq/server.properties')
meta = properties('/var/lib/automq/metadata/meta.properties')
listeners = dict(item.split('://', 1) for item in props['listeners'].split(','))
advertised = dict(item.split('://', 1) for item in props['advertised.listeners'].split(','))
container = subprocess.run(['docker', 'inspect', '--format',
    '{{json .State.Status}} {{json .State.Health.Status}} {{json .Image}}', 'automq'],
    capture_output=True, text=True, check=True).stdout.strip().split()
print(json.dumps({
    'architecture': platform.machine(),
    'instance_id': Path('/var/lib/cloud/data/instance-id').read_text().strip(),
    'node_id': props['node.id'],
    'metadata': {key: meta[key] for key in ('cluster.id', 'node.id', 'directory.id')},
    'secret_bundle_sha256': checksum('/etc/automq/secrets/secrets.env'),
    'ca_sha256': hashlib.sha256(ca_der).hexdigest(),
    'server_config_sha256': checksum('/etc/automq/server.properties'),
    'container_status': json.loads(container[0]),
    'container_health': json.loads(container[1]),
    'container_image': json.loads(container[2]),
    'private_host': listeners['INTERNAL'].rsplit(':', 1)[0],
    'internal_port': int(listeners['INTERNAL'].rsplit(':', 1)[1]),
    'controller_port': int(listeners['CONTROLLER'].rsplit(':', 1)[1]),
    'public_host': advertised['EXTERNAL'].rsplit(':', 1)[0],
    'external_port': int(advertised['EXTERNAL'].rsplit(':', 1)[1])}, sort_keys=True))
'''

PRIVATE_TCP = r'''
import json, socket, sys
result = []
for node in json.loads(sys.argv[1]):
    for role in ('controller', 'internal'):
        port = node[role + '_port']
        try:
            with socket.create_connection((node['private_host'], port), timeout=3):
                opened = True
        except OSError:
            opened = False
        result.append({'target_node': node['node_id'], 'role': role, 'port': port, 'open': opened})
print(json.dumps(result))
'''


def remote(alias, script, argument=None):
    command = 'sudo python3 -' + ((' ' + shlex.quote(json.dumps(argument))) if argument is not None else '')
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                             '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
                             alias, command], input=script, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError('remote node capture failed')
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='automq-oci')
    parser.add_argument('--nodes', type=int, default=3)
    parser.add_argument('--cluster-id', required=True)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    nodes = []
    for ordinal in range(args.nodes):
        alias = args.profile + '-' + str(ordinal)
        node = remote(alias, FACTS)
        node['alias'] = alias
        nodes.append(node)
    private = {node['alias']: remote(node['alias'], PRIVATE_TCP, nodes) for node in nodes}
    public = []
    for node in nodes:
        for role in ('external', 'controller', 'internal'):
            port = node[role + '_port']
            try:
                with socket.create_connection((node['public_host'], port), timeout=3):
                    opened = True
            except OSError:
                opened = False
            public.append({'node': node['node_id'], 'role': role, 'port': port,
                           'open': opened, 'expected_open': role == 'external'})
    checks = {
        'all_nodes_arm64': all(node['architecture'] == 'aarch64' for node in nodes),
        'distinct_instances': len({node['instance_id'] for node in nodes}) == args.nodes,
        'expected_node_ids': {node['node_id'] for node in nodes} == {str(i) for i in range(args.nodes)},
        'same_cluster_id': all(node['metadata']['cluster.id'] == args.cluster_id for node in nodes),
        'formatted_node_ids_match': all(node['metadata']['node.id'] == node['node_id'] for node in nodes),
        'same_secret_bundle': len({node['secret_bundle_sha256'] for node in nodes}) == 1,
        'same_ca': len({node['ca_sha256'] for node in nodes}) == 1,
        'containers_running_healthy': all(node['container_status'] == 'running' and node['container_health'] == 'healthy' for node in nodes),
        'all_private_connections_open': all(row['open'] for rows in private.values() for row in rows),
        'public_boundary': all(row['open'] == row['expected_open'] for row in public),
    }
    if args.compare:
        previous = json.loads(args.compare.read_text())
        if previous['profile'] != args.profile:
            raise ValueError('comparison profile mismatch')
        fields = ('instance_id', 'metadata', 'secret_bundle_sha256', 'ca_sha256', 'server_config_sha256', 'container_image')
        before = {node['alias']: {field: node[field] for field in fields} for node in previous['nodes']}
        after = {node['alias']: {field: node[field] for field in fields} for node in nodes}
        checks['identities_and_credentials_unchanged'] = before == after
    result = {'profile': args.profile, 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'nodes': nodes, 'private_tcp': private, 'public_tcp': public,
              'checks': checks, 'passed': all(checks.values())}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as error:
        print(json.dumps({'passed': False, 'error': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
