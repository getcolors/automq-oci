#!/usr/bin/env python3
"""Revoke only this test's operator key after the managed state bucket is gone."""
from datetime import datetime, timezone
import json
import os
import subprocess

USER = 'ocid1.user.oc1..aaaaaaaa2h257qc2khmtwck3pgat6bg2dah5hnwtpyjogxp3gfkxu4qgiqdq'
BUCKET = 'automq-oci-state-frqagv4ppzaz'
NAMESPACE = 'frqagv4ppzaz'
NAME = 'automq-oci-state-backend'


def run(*args):
    return subprocess.run(['oci', *args, '--auth', 'security_token'],
                          capture_output=True, text=True, timeout=120)


def keys():
    result = run('iam', 'customer-secret-key', 'list', '--user-id', USER, '--all')
    if result.returncode:
        raise RuntimeError('Cannot verify operator key inventory')
    return json.loads(result.stdout)['data'] if result.stdout.strip() else []


def main():
    key_id = os.environ['COLORS_PAR_OCI_ACCESS_KEY_ID']
    existing = [key for key in keys() if key['id'] == key_id]
    if existing and (len(existing) != 1 or existing[0]['display-name'] != NAME):
        raise RuntimeError('Key does not match the separate test credential')
    result = run('os', 'bucket', 'get', '--namespace-name', NAMESPACE, '--bucket-name', BUCKET)
    try:
        absent = result.returncode != 0 and json.loads(result.stderr[result.stderr.index('{'):])['status'] == 404
    except (ValueError, KeyError):
        absent = False
    if not absent:
        raise RuntimeError('State bucket absence not verified; credential retained')
    if existing:
        deleted = run('iam', 'customer-secret-key', 'delete', '--user-id', USER,
                      '--customer-secret-key-id', key_id, '--force')
        if deleted.returncode:
            raise RuntimeError('Credential revocation failed')
    if any(key['id'] == key_id for key in keys()):
        raise RuntimeError('Credential still appears in native inventory')
    print(json.dumps({'revoked': True, 'display_name': NAME,
                      'state_bucket_absent_before_revocation': True,
                      'native_key_absence_verified': True,
                      'already_absent_on_this_check': not bool(existing),
                      'verified_at_utc': datetime.now(timezone.utc).isoformat()}, indent=2))


if __name__ == '__main__':
    main()
