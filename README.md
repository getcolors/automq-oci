# AutoMQ on OCI

Configuration and live test evidence for three AutoMQ broker/controllers in OCI
Frankfurt under profile `automq-oci`. Three A1 instances are running, one per
availability domain, with 1 OCPU and 8 GiB each. The final published source
passed all 16 public Kafka gates and nine host gates, including abrupt broker
failure and exact record recovery. Two earlier full converges also passed.

The [September 11 test](verification-2026-09-11.md) used a different tenancy and
reached storage provisioning and cleanup without launching a VM. Its historical
results remain separate from this running cluster.

The deployment owns separate OCI state, data and operations buckets. OpenTofu state
and AutoMQ records use OCI's S3-compatible endpoint. Journal and application
coordination updates use OCI's native API to enforce conditional writes. No AWS or Cloudflare bucket is used.

`colors.yml` contains non-secret desired state. The OCI provider uses the shared
`DEFAULT` session in `~/.oci/config`. Ignored `.envrc.private` supplies
`COLORS_PAR_OCI_ACCESS_KEY_ID` and `COLORS_PAR_OCI_SECRET_ACCESS_KEY` for state.
The application storage stage creates a separate identity for its two buckets.

```sh
./green build
./green create --dry-run
./green create
```

The configured public Kafka listener uses SASL_SSL, SCRAM-SHA-512 and a private CA.
Acceptance exports the public CA to
`.colors-a1/automq-oci/automq-acceptance/ca.crt`. Retrieve the client credential
with `ssh automq-oci sudo automq-credential`. The client may access topics and
groups under `colors-`.

The deployment uses the existing OCI subnet named in `colors.yml`. Its network
security group and machines belong to this deployment. The shared subnet and
VCN remain outside its lifecycle.

The committed destruction guard stays enabled. Lifecycle testing uses the
one-run override `COLORS_PAR_COMPUTE_PREVENT_DESTROY=false ./green delete`.
Deletion removes stored records with the owned application buckets and removes
the state bucket last. The test's separately created backend credential must
also be revoked after state finalization. The September 11 credential was revoked. The September 12 state credential
remains in the ignored private file while this cluster is running.

See [verification.md](verification.md) for the observed results, limitations
and cleanup scope.
