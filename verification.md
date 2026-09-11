# OCI live verification

**Result:** storage provisioning and conditional-write probes ran against OCI.
No broker VM launched, so Kafka acceptance, continuity and repeat full
convergence were not tested.

Profile `automq-oci` requests three combined AutoMQ broker/controllers in OCI
`eu-frankfurt-1`. State, data and ops use separate OCI buckets. The test uses
the existing subnet in `colors.yml` and a deployment-owned network security
group. The subnet and VCN remain shared resources.

## Verified storage preconditions

The isolated [S3 compatibility probe](evidence/compat-preconditions.json)
accepted its initial conditional create and refused a competitor with HTTP
412. It accepted both an exact-ETag replacement and a stale-ETag replacement.
The final object contained the stale writer's value. OCI's compatibility API
therefore did not enforce the tested PUT `If-Match` contract.

The [native API probe](evidence/native-preconditions.json) accepted the current
ETag, refused the stale ETag with HTTP 412, and preserved the newer value.
Coordination must use this native contract. AutoMQ record storage continues
to use the S3-compatible endpoint.

Both probes used isolated temporary buckets and objects. The operator removed
them after the tests.

## Credential readiness

The test reused the `DEFAULT` OCI session used by sibling deployments.
No sibling supplied an OCI customer secret key, so the operator created a
separate state credential and saved it in ignored `.envrc.private` with mode
0600. It remains separate from the application's scoped storage identity.

The [sampled authentication record](evidence/credential-propagation.json)
shows a last failure 7 minutes 15 seconds after creation and a first success
8 minutes 15 seconds after creation. The same credential and configuration
served both requests. This observation does not establish a general OCI
propagation bound.

## Provisioning attempts

The [initial native inventory](evidence/resources-initial.json) found no
deployment resources. Build and dry-run passed after OCI backend and private
ingress support were added.

The first create produced the state bucket and network security group, then
failed all three VM stages. The [independent inventory](evidence/resources-after-create-1.json)
confirmed that no VM or boot volume remained from that attempt.

The [attempt history](evidence/compute-attempts.json) records the later failures.
E4 with 1 OCPU/8 GB failed with a memory-ratio range of 0 to 0. A2 failed with
explicit 8 GB, explicit 6 GB, omitted memory, and a final 2-vCPU request.
A1 with default memory failed with "Out of host capacity" in all three ADs.
The [A2 shape response](evidence/a2-raw-shape-options.json) also advertised
zero minimum and maximum memory ratios. These observations do not establish
the cause of Oracle's ratio error. A2 quota was available.

The subnet is regional. The final attempt distributed the three desired nodes
across AD1, AD2 and AD3 and used an ARM Ubuntu image compatible with A2. The
pinned AutoMQ image is a multiarchitecture index containing amd64 and arm64.
No attempt reached bootstrap, TLS, Kafka or failover gates.

Failed applies left empty Terraform states and failed journal entries.
Recovery used the package's explicit operation-ID checks, native paginated
instance/boot-volume absence checks and native journal CAS before retry.

## Application storage

The real package storage stage ran independently after compute failed. It
created the data and ops buckets and a dedicated user, group, policy, customer
secret key and native API signing key. OCI Identity Domains initially rejected
the user with ["The primary email must be specified."](evidence/storage-stage-email-error.json)
The package now requires a tenancy-unique `automq-oci-user-email`.

The [scoped storage probe](evidence/storage-stage.json) passed. Both application
buckets returned exact synthetic bytes after write/read and allowed cleanup.
The actual package helper accepted conditional creation, rejected a competing
create, accepted native replacement with the current ETag, and rejected a
stale ETag. Its lease helper allowed an initial holder, refused a competitor,
allowed takeover after expiry, refused the old holder's release, excluded a
third holder, and allowed the current holder to renew and release.

The application S3 credential received HTTP 404 for state bucket head/list,
owner-object read and object write. Its native signing identity also received
404 for state owner-object head and object write. An independent operator
request proved the state bucket and owner object existed before those checks,
so these results demonstrate access denial rather than a missing resource.

A [second successful storage create](evidence/storage-stage-repeat.json)
completed. An independent Terraform plan returned exit code 0 with no changes.
The earlier failed user-creation apply is counted separately in that evidence.
These were synthetic object operations. No Kafka record reached object storage.

## Lifecycle deletion

The default `./green delete` refused with exit code 2 while the committed
`compute-prevent-destroy: true` guard remained enabled.

The [pre-delete native inventory](evidence/resources-before-delete.json)
records three owned buckets, one network security group, the application
user/group/policy, its customer secret key and native API key, and zero VMs or
boot volumes. The state bucket held object versions and delete markers.

The [published partial-delete attempt](evidence/delete-lifecycle.json) routed
straight to backend finalization and refused with
`managed backend finalization refused; live or unowned state remains`.
It skipped application storage and the shared network group because no node
was ready. This refusal preserved the state bucket. A subsequent intermediate
implementation reached SSH cleanup but required unavailable broker addresses.
The local SSH updater also rejected an empty host list during removal with
`invalid SSH host inventory`. These paths required fixes before normal deletion
could complete. No storage or infrastructure destroy stage ran in those attempts.

The next delete passed storage and compute cleanup. The
[independent resource audit](evidence/resources-after-resource-delete.json)
found no application buckets, IAM resources, network security group, VMs,
volumes, public IPs or local SSH files/aliases. The state bucket remained with
278 versions, including 64 delete markers. Its finalization required a separate
retry after a [metadata-update failure](evidence/backend-finalize-error.json).
OCI returned 404 to a bucket metadata PUT because UpdateBucket requires POST.
After that fix, inspection also needed to recognize a valid retired journal
before checking its retained finalizer lock. Active journals still require an
idle lock. These changes allow normal deletion to resume from the saved
`deleting` marker.

Normal deletion then resumed successfully with exit code 0. Finalization took
242.549 seconds and removed every state version and the bucket last. The
[final native inventory](evidence/resources-after-delete.json) found no owned
buckets, compute resources, IAM resources, public IPs or local SSH artifacts.
The shared subnet and VCN remained available.

The final published launcher repeated deletion with exit code 0 and no local
source overrides. A [second native audit](evidence/resources-after-repeat-delete.json)
confirmed that it recreated no resources. The separately created operator
backend key was then [revoked and verified absent](evidence/backend-credential-revocation.json).
The ignored private credential file was removed after the final secret scan.

## Published validation and scope

The final installed package comes from AutoMQ repository commit
`d37766dbac4f115f543fa55cdbd3f25d46a4fc32`. Its launcher pins AutoMQ source
`ace2f656236741df3d92a6319599e27fd7dbd11f` and compute source
`58ac766d17cc1b174992986c1088d9d7045e13b5`.
[Build and create dry-run passed](evidence/published-validation.json) without
local source overrides. The copied skill and installer lockfile are committed.

Live development attempts used source overrides while fixing the defects above.
The final published version passed repeat deletion, build and dry-run. No full
create reached broker bootstrap at any pin. TLS, Kafka ACLs, quorum, failover,
record continuity and repeated full convergence therefore remain unverified
for this OCI deployment.
