#!/usr/bin/env python3
"""Probe package-owned OCI storage before any AutoMQ broker is started.

The storage-stage driver supplies scoped credentials only through environment
variables. Output contains booleans, HTTP statuses and elapsed time, never keys.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from urllib.error import HTTPError
import uuid

from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='JSON non-secret desired state')
    parser.add_argument('--store-module', required=True)
    args = parser.parse_args()
    opts = json.loads(args.config)
    for name in ('R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'OCI_SIGNING_KEY_B64', 'OCI_SIGNING_KEY_ID'):
        os.environ['AUTOMQ_' + name] = os.environ['COLORS_PAR_AUTOMQ_' + name]
    spec = importlib.util.spec_from_file_location('package_store', args.store_module)
    store = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(store)
    native = ['oci', '--profile', opts['oci-config-file-profile'], '--region', opts['oci-region'], '--auth', 'security_token']
    state_args = ['--namespace-name', opts['oci-namespace'], '--bucket-name', opts['oci-bucket']]
    for command in (['os', 'bucket', 'get'] + state_args,
                    ['os', 'object', 'head'] + state_args + ['--name', '_colors/backend-owner.json']):
        result = subprocess.run(native + command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError('operator could not independently verify state bucket and owner marker')
    probe_args = SimpleNamespace(profile=opts['profile'], endpoint=opts['automq-r2-endpoint'], region=opts['automq-r2-region'],
                                 data_bucket=opts['automq-data-r2-bucket'], ops_bucket=opts['automq-ops-r2-bucket'])
    started = time.monotonic()
    attempts = 0
    checks = {'operator_verified_state_bucket_and_marker': True, 'package_conditional_preconditions': True}
    while True:
        attempts += 1
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                store.cmd_preconditions(probe_args)
            s3 = store.client(probe_args.endpoint, probe_args.region)
            for role, bucket in [('data', probe_args.data_bucket), ('ops', probe_args.ops_bucket)]:
                checks[role + '_list_http'] = s3.list_objects_v2(Bucket=bucket, MaxKeys=1)['ResponseMetadata']['HTTPStatusCode']
                object_key = '_colors/' + opts['profile'] + '/roundtrip/' + uuid.uuid4().hex
                body = b'synthetic package storage acceptance; no Kafka record'
                try:
                    s3.put_object(Bucket=bucket, Key=object_key, Body=body)
                    checks[role + '_object_roundtrip'] = s3.get_object(Bucket=bucket, Key=object_key)['Body'].read() == body
                    if not checks[role + '_object_roundtrip']:
                        raise RuntimeError('allowed object roundtrip changed bytes')
                finally:
                    s3.delete_object(Bucket=bucket, Key=object_key)
            break
        except Exception as error:
            if time.monotonic() - started >= 900:
                raise RuntimeError('scoped storage credentials or preconditions did not become ready within the retry window') from error
            time.sleep(15)
    forbidden_key = '_colors/' + opts['profile'] + '/scope-probe/' + uuid.uuid4().hex
    operations = [('head_bucket', {}), ('list_objects_v2', {'MaxKeys': 1}),
                  ('get_object', {'Key': '_colors/backend-owner.json'}),
                  ('put_object', {'Key': forbidden_key, 'Body': b'forbidden-scope-probe'})]
    for name, parameters in operations:
        try:
            getattr(s3, name)(Bucket=opts['oci-bucket'], **parameters)
        except ClientError as error:
            status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            checks['state_' + name + '_http'] = status
            if status not in (403, 404):
                raise RuntimeError('unexpected status in scoped state denial probe')
        else:
            if name == 'put_object':
                s3.delete_object(Bucket=opts['oci-bucket'], Key=forbidden_key)
            raise RuntimeError('application S3 credential can access state storage')
    for method, key, body in [('HEAD', '_colors/backend-owner.json', None), ('PUT', forbidden_key, b'forbidden-native-probe')]:
        try:
            with store._oci_request(s3, method, opts['oci-bucket'], key, body):
                if method == 'PUT':
                    with store._oci_request(s3, 'DELETE', opts['oci-bucket'], key):
                        pass
                raise RuntimeError('application native signing identity can access state storage')
        except HTTPError as error:
            checks['native_state_' + method.lower() + '_http'] = error.code
            if error.code not in (403, 404):
                raise RuntimeError('unexpected native scoped state denial status')
    lease_name = 'storage-stage-' + uuid.uuid4().hex
    lease_key = store.key(opts['profile'], 'lease', lease_name + '.json')
    def lease(function, holder, ttl=60):
        values = SimpleNamespace(**vars(probe_args), name=lease_name, holder=holder, ttl=ttl)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            function(values)
        return json.loads(output.getvalue())
    try:
        checks['lease_initial_acquire'] = lease(store.cmd_lease_acquire, 'stage-A', 10)['acquired']
        checks['lease_competitor_denied'] = not lease(store.cmd_lease_acquire, 'stage-B')['acquired']
        if not checks['lease_initial_acquire'] or not checks['lease_competitor_denied']:
            raise RuntimeError('initial lease exclusion failed')
        time.sleep(11)
        checks['lease_expired_takeover'] = lease(store.cmd_lease_acquire, 'stage-B')['acquired']
        checks['lease_stale_release_denied'] = not lease(store.cmd_lease_release, 'stage-A')['released']
        checks['lease_new_holder_excludes'] = not lease(store.cmd_lease_acquire, 'stage-C')['acquired']
        checks['lease_holder_renewal'] = lease(store.cmd_lease_acquire, 'stage-B')['acquired']
        checks['lease_current_release'] = lease(store.cmd_lease_release, 'stage-B')['released']
        if not all(value for name, value in checks.items() if name.startswith('lease_')):
            raise RuntimeError('native lease transition failed')
    finally:
        s3.delete_object(Bucket=probe_args.ops_bucket, Key=lease_key)
    print(json.dumps({'passed': True, 'scope': 'package storage stage only; no broker acceptance',
                      'profile': opts['profile'], 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
                      'credential_readiness_attempts': attempts, 'credential_readiness_seconds': round(time.monotonic() - started, 2),
                      'checks': checks}, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
