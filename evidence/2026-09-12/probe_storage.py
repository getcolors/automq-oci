#!/usr/bin/env python3
"""Probe application OCI credentials and isolated leases on an existing node."""
import argparse
import json
import subprocess
import sys

REMOTE = r'''
import json, os, subprocess, sys, time, uuid
import importlib.util
from botocore.exceptions import ClientError
opts = json.loads(sys.argv[1])
for line in open('/etc/automq/store.env'):
    name, separator, value = line.strip().partition('=')
    if separator:
        os.environ[name] = value
spec = importlib.util.spec_from_file_location('deployed_store', '/usr/local/lib/automq-store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)
s3 = store.client(opts['endpoint'], opts['region'])
result = {'profile': opts['profile'], 'checks': {}}
checks = result['checks']
for role in ('data', 'ops'):
    total = 0
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=opts[role]):
        total += sum(not obj['Key'].startswith('_colors/') for obj in page.get('Contents', []))
    checks[role + '_objects'] = total
    assert total > 0, role + ' bucket has no application objects'
forbidden_key = '_colors/' + opts['profile'] + '/denial-probe/' + uuid.uuid4().hex
for operation in ('head_bucket', 'list_objects_v2', 'get_object', 'put_object'):
    try:
        getattr(s3, operation)(Bucket=opts['state'], **({'Key': '_colors/backend-owner.json'} if operation == 'get_object' else {'Key': forbidden_key, 'Body': b'forbidden-state-probe'} if operation == 'put_object' else {}))
    except ClientError as exc:
        status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        checks['state_' + operation + '_http'] = status
        assert status in (403, 404), 'state denial must be HTTP 403 or concealed HTTP 404'
    else:
        if operation == 'put_object':
            s3.delete_object(Bucket=opts['state'], Key=forbidden_key)
        raise AssertionError('application credentials can access state bucket')
from urllib.error import HTTPError
for method, object_key, body in [('HEAD', '_colors/backend-owner.json', None), ('PUT', forbidden_key, b'forbidden-native-state-probe')]:
    try:
        with store._oci_request(s3, method, opts['state'], object_key, body):
            if method == 'PUT':
                with store._oci_request(s3, 'DELETE', opts['state'], object_key):
                    pass
            raise AssertionError('application API signing key can access state bucket')
    except HTTPError as exc:
        checks['native_state_' + method.lower() + '_http'] = exc.code
        assert exc.code in (403, 404)
name = 'evidence-' + uuid.uuid4().hex
lease_key = '_colors/' + opts['profile'] + '/lease/' + name + '.json'
cas_key = '_colors/' + opts['profile'] + '/evidence/' + name + '.json'
def lease(command, holder, ttl=None):
    args = ['/usr/local/bin/automq-store', command, '--name', name, '--holder', holder]
    if ttl is not None:
        args += ['--ttl', str(ttl)]
    value = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if value.returncode:
        raise RuntimeError('isolated lease command failed')
    return json.loads(value.stdout)
try:
    checks['initial_acquire'] = lease('lease-acquire', 'evidence-A', 10)['acquired']
    checks['competitor_denied'] = not lease('lease-acquire', 'evidence-B', 60)['acquired']
    assert checks['initial_acquire'] and checks['competitor_denied']
    time.sleep(11)
    checks['expired_takeover'] = lease('lease-acquire', 'evidence-B', 60)['acquired']
    checks['stale_release_denied'] = not lease('lease-release', 'evidence-A')['released']
    checks['new_holder_still_excludes'] = not lease('lease-acquire', 'evidence-C', 60)['acquired']
    assert all(checks[key] for key in ('expired_takeover', 'stale_release_denied', 'new_holder_still_excludes'))
    checks['holder_renewal'] = lease('lease-acquire', 'evidence-B', 60)['acquired']
    assert checks['holder_renewal']
    checks['current_release'] = lease('lease-release', 'evidence-B')['released']
    assert checks['current_release']
    def conditional(body, header, value):
        def condition(request, **kwargs):
            request.headers.add_header(header, value)
        s3.meta.events.register('before-sign.s3.PutObject', condition)
        try:
            return s3.put_object(Bucket=opts['ops'], Key=cas_key, Body=body)
        finally:
            s3.meta.events.unregister('before-sign.s3.PutObject', condition)
    first = conditional(b'first', 'If-None-Match', '*')
    checks['conditional_initial_create'] = True
    try:
        conditional(b'competitor', 'If-None-Match', '*')
    except ClientError as exc:
        checks['competing_create_http'] = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        assert checks['competing_create_http'] == 412
    else:
        raise AssertionError('OCI accepted competing conditional create')
    # The compatibility API's ignored PUT If-Match is captured independently
    # in compat-preconditions.json. Exercise the deployed native lease path.
    etag = store._etag(s3, opts['ops'], cas_key)
    checks['native_etag_present'] = bool(etag)
    assert checks['native_etag_present']
    checks['exact_native_etag_replace'] = store._put_if_match(s3, opts['ops'], cas_key, {'value': 'second'}, etag)
    checks['stale_native_etag_rejected'] = not store._put_if_match(s3, opts['ops'], cas_key, {'value': 'stale'}, etag)
    assert checks['exact_native_etag_replace'] and checks['stale_native_etag_rejected']
    checks['conditional_write_preserved_value'] = store.get_json(s3, opts['ops'], cas_key) == {'value': 'second'}
    assert checks['conditional_write_preserved_value']
finally:
    for key in (lease_key, cas_key):
        s3.delete_object(Bucket=opts['ops'], Key=key)
result['passed'] = True
print(json.dumps(result, indent=2, sort_keys=True))
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='automq-oci')
    parser.add_argument('--oci-profile', default='DEFAULT')
    parser.add_argument('--oci-auth', choices=['security_token', 'api_key'], default='security_token')
    parser.add_argument('--region', default='eu-frankfurt-1')
    parser.add_argument('--namespace', default='fryovegrk8e7')
    parser.add_argument('--endpoint', default='https://fryovegrk8e7.compat.objectstorage.eu-frankfurt-1.oraclecloud.com')
    for role in ('state', 'data', 'ops'):
        parser.add_argument('--' + role, default=f'automq-oci-{role}-fryovegrk8e7')
    args = parser.parse_args()
    # OCI can conceal forbidden resources as 404. Prove existence independently
    # with operator credentials before interpreting the application's responses.
    native = {}
    for kind in ('bucket', 'object'):
        command = ['oci', 'os', kind, 'get' if kind == 'bucket' else 'head',
                   '--namespace-name', args.namespace, '--bucket-name', args.state,
                   '--region', args.region, '--profile', args.oci_profile, '--auth', args.oci_auth]
        if kind == 'object':
            command += ['--name', '_colors/backend-owner.json']
        probe = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if probe.returncode:
            print(json.dumps({'passed': False, 'error': 'native state existence probe failed', 'resource': kind}))
            return 1
        native[kind + '_exists'] = True
    import shlex
    command = 'sudo python3 - ' + shlex.quote(json.dumps(vars(args)))
    run = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                          args.profile + '-0', command], input=REMOTE,
                         capture_output=True, text=True, timeout=300)
    if run.returncode:
        print(json.dumps({'passed': False, 'error': 'remote storage probe failed', 'returncode': run.returncode}))
        return 1
    result = json.loads(run.stdout)
    result['native_state_verification'] = native
    from datetime import datetime, timezone
    result['verified_at_utc'] = datetime.now(timezone.utc).isoformat()
    result['scope'] = 'installed broker storage client, application objects, state denials and isolated leases'
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        print(json.dumps({'passed': False, 'error': type(exc).__name__}))
        sys.exit(1)
