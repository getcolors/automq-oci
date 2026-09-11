#!/usr/bin/env python3
"""Audit deployment resources through OCI, independently of OpenTofu state."""
import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

PROFILE = 'automq-oci'
COMPARTMENT = 'ocid1.tenancy.oc1..aaaaaaaabz6wkmand36zaln2wbyuqqflr2uzohoj3briepj4mfpohas2bxsq'
NAMESPACE = 'frqagv4ppzaz'


def oci(*args):
    run = subprocess.run(['oci', *args, '--auth', 'security_token'],
                         capture_output=True, text=True, timeout=180)
    if run.returncode:
        raise RuntimeError('OCI inventory command failed: ' + ' '.join(args[:3]))
    return json.loads(run.stdout).get('data', []) if run.stdout.strip() else []


def owned(items):
    return [item for item in items
            if PROFILE in (item.get('display-name') or item.get('name') or '')]


def prior_ids():
    """Keep IDs from earlier independent snapshots after attachments disappear."""
    found = {key: set() for key in ('instances', 'boot_volumes', 'volumes', 'vnics')}
    for path in Path(__file__).parent.glob('resources-*.json'):
        content = path.read_text()
        if not content.strip():
            continue  # The shell may have opened this invocation's output file.
        snapshot = json.loads(content)
        if snapshot.get('profile') != PROFILE:
            continue
        for key in found:
            found[key].update(item['id'] for item in snapshot.get(key, []) if 'id' in item)
    return found


def inventory():
    common = ['--compartment-id', COMPARTMENT, '--all']
    commands = {
        'instances': ['compute', 'instance', 'list', *common],
        'network_security_groups': ['network', 'nsg', 'list', *common],
        'buckets': ['os', 'bucket', 'list', '--namespace-name', NAMESPACE, *common],
        'users': ['iam', 'user', 'list', *common],
        'groups': ['iam', 'group', 'list', *common],
        'policies': ['iam', 'policy', 'list', *common],
        'volumes': ['bv', 'volume', 'list', *common],
        'reserved_public_ips': ['network', 'public-ip', 'list', '--scope', 'REGION', '--lifetime', 'RESERVED', *common],
    }
    result = {'profile': PROFILE, 'observed_at': datetime.now(timezone.utc).isoformat()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(oci, *args): name for name, args in commands.items()}
        for future in concurrent.futures.as_completed(futures):
            result[futures[future]] = owned(future.result())
    known = prior_ids()
    instance_ids = known['instances'] | {item['id'] for item in result['instances']}
    result['boot_volumes'] = []
    result['boot_volume_attachments'] = []
    for ad in oci('iam', 'availability-domain', 'list'):
        attachments = oci('compute', 'boot-volume-attachment', 'list',
                          '--availability-domain', ad['name'], *common)
        attachments = [item for item in attachments if item['instance-id'] in instance_ids]
        result['boot_volume_attachments'].extend(attachments)
        boot_ids = known['boot_volumes'] | {item['boot-volume-id'] for item in attachments}
        volumes = oci('bv', 'boot-volume', 'list', '--availability-domain', ad['name'], *common)
        result['boot_volumes'].extend(item for item in volumes
                                     if item['id'] in boot_ids or item in owned(volumes))
    attachments = oci('compute', 'volume-attachment', 'list', *common)
    result['volume_attachments'] = [item for item in attachments if item['instance-id'] in instance_ids]
    volume_ids = known['volumes'] | {item['volume-id'] for item in result['volume_attachments']}
    result['volumes'] = [item for item in oci('bv', 'volume', 'list', *common)
                         if item['id'] in volume_ids or PROFILE in (item.get('display-name') or '')]
    attachments = oci('compute', 'vnic-attachment', 'list', *common)
    result['vnic_attachments'] = [item for item in attachments if item['instance-id'] in instance_ids]
    result['vnics'] = [oci('network', 'vnic', 'get', '--vnic-id', item['vnic-id'])
                       for item in result['vnic_attachments'] if item['lifecycle-state'] == 'ATTACHED']
    result['public_ips'] = [dict(vnic_id=item['id'], public_ip=item['public-ip'])
                            for item in result['vnics'] if item.get('public-ip')]
    result['customer_secret_keys'] = []
    result['api_keys'] = []
    for user in result['users']:
        keys = oci('iam', 'customer-secret-key', 'list', '--user-id', user['id'], '--all')
        signing_keys = oci('iam', 'user', 'api-key', 'list', '--user-id', user['id'], '--all')
        result['api_keys'].extend(
            {'user_id': user['id'], **{key: item.get(key) for key in ('fingerprint', 'time-created')}}
            for item in signing_keys)
        # Never emit a credential value even if the CLI's response shape changes.
        result['customer_secret_keys'].extend(
            {'user_id': user['id'], **{key: item.get(key) for key in ('display-name', 'time-created')}}
            for item in keys)

    result['bucket_details'] = {}
    result['bucket_version_counts'] = {}
    for bucket in result['buckets']:
        result['bucket_details'][bucket['name']] = oci('os', 'bucket', 'get',
            '--namespace-name', NAMESPACE, '--bucket-name', bucket['name'])
        versions = oci('os', 'object', 'list-object-versions', '--namespace-name',
                       NAMESPACE, '--bucket-name', bucket['name'], '--all')
        result['bucket_version_counts'][bucket['name']] = {
            'versions': len(versions),
            'bytes': sum(item.get('size') or 0 for item in versions),
            'delete_markers': sum(bool(item.get('is-delete-marker')) for item in versions),
        }
    result['active_instances'] = [item for item in result['instances']
                                  if item['lifecycle-state'] != 'TERMINATED']
    result['active_boot_volumes'] = [item for item in result['boot_volumes']
                                     if item['lifecycle-state'] != 'TERMINATED']
    result['active_volumes'] = [item for item in result['volumes']
                                if item['lifecycle-state'] != 'TERMINATED']
    result['scope_note'] = 'The configured subnet and VCN predate this deployment and are shared.'
    subnet = oci('network', 'subnet', 'get', '--subnet-id',
                 'ocid1.subnet.oc1.eu-frankfurt-1.aaaaaaaaeo3hhvldibbw42m6atn52izm6vmaxxcjbfm2fz7t3glvw3ovihta')
    vcn = oci('network', 'vcn', 'get', '--vcn-id', subnet['vcn-id'])
    result['retained_shared_network'] = {
        'subnet_id': subnet['id'], 'subnet_state': subnet['lifecycle-state'],
        'vcn_id': vcn['id'], 'vcn_state': vcn['lifecycle-state'],
    }
    result['historical_resource_ids'] = {key: sorted(values) for key, values in known.items()}
    ssh = Path.home() / '.ssh'
    for key, name in [('private_key', PROFILE), ('public_key', PROFILE + '.pub'),
                      ('known_hosts', PROFILE + '.known_hosts'),
                      ('key_lock', '.' + PROFILE + '.colors-key.lock')]:
        result['local_' + key + '_exists'] = (ssh / name).exists()
    result['local_ssh_alias_exists'] = (ssh / 'config').exists() and PROFILE in (ssh / 'config').read_text()
    return result


if __name__ == '__main__':
    print(json.dumps(inventory(), indent=2, sort_keys=True))
