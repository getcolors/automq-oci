# OCI live verification

The September 12 deployment runs three A1 AutoMQ broker/controllers in a different
OCI tenancy from the [September 11 test](verification-2026-09-11.md). All three
VMs launched. Two full converges on source `5aac5a3` passed all 16 public gates.
The final source `6b76c01` also passed all 16 public gates and nine host gates.

## Deployment

Profile `automq-oci` uses `VM.Standard.A1.Flex`, 1 OCPU and 8 GiB per node,
distributed across Frankfurt's three availability domains. The image is Ubuntu
24.04 ARM64. AutoMQ uses the immutable 1.7.4 multiarchitecture image index in
`colors.yml`. [Native discovery](evidence/2026-09-12/account-preflight.json)
confirmed A1 availability, the image and the shared regional subnet.

The deployment owns three OCI buckets: state, data and ops. State and broker
records use OCI's S3 compatibility endpoint; ownership coordination and restart
leases use the native OCI API. No AWS or Cloudflare bucket participates. The
shared subnet and VCN remain outside the deployment lifecycle.

## Readiness and adoption

The existing `DEFAULT` OCI login was reused. No sibling project supplied a
customer secret key, so a separate state backend credential was created and
saved only in ignored `.envrc.private`. The application stage generated its
own bucket-scoped identity, customer secret key and API signing key.

Successful ListBuckets did not establish GetObject readiness. The first create
failed before VM dispatch on a compatibility state read. The second launched
all three VMs but failed on the adoption marker GetObject after a narrower
precondition gate passed. The [attempt history](evidence/2026-09-12/converge-attempts.json)
and [prerequisite observations](evidence/2026-09-12/compute-prerequisite-readiness.json)
record the exact failures. No signing-key or endpoint change was needed for the
state read to become available.

A subsequent attempt passed authentication but refused adoption because two
failed readiness attempts had left test objects in the ops bucket. The operator
verified their exact names and synthetic bodies, removed only those two keys,
and proved the bucket empty. [Cleanup evidence](evidence/2026-09-12/precondition-leftovers.json)
preserves that scope. No ownership or genesis marker was forged. The source fix
records exact probe keys before writes and requires cleanup across retries
before the gate can succeed.

## Firewall and reboot

The original image had native INPUT rejection even while UFW was inactive.
The package's scoped native chain admits public Kafka from the configured CIDRs
and controller/internal traffic only from the three peer IPs. Initial
[firewall checks](evidence/2026-09-12/firewall-applied.json) preserved unowned
rules and were unchanged on a second application. Temporary listener probes
passed 18 private and 9 public connection checks; these were TCP checks, not
Kafka acceptance.

A real reboot exposed a separate defect: the base package installation had
removed OCI's native persistence packages when installing UFW. AutoMQ's owned
chain returned, but the original platform INPUT/FORWARD rejection and
InstanceServices OUTPUT protections did not. The failed
[reboot audit](evidence/2026-09-12/reboot-node-1.json) preserves the before/after
rules. Broker health and all 50 independent continuity records survived that
reboot. The [repair receipts](evidence/2026-09-12/platform-persistence-repair.json)
show native persistence restored on all three nodes without flushing live
rules or replacing the saved platform file. The
[second real reboot](evidence/2026-09-12/reboot-node-1-after-persistence-repair.json)
passed all eight checks: same machine, new boot, enabled/active firewall, first
owned INPUT jump, unchanged owned and platform rules, healthy broker, and
private ports reachable from both peers. The 50 continuity records
[remained exact](evidence/2026-09-12/continuity-after-repaired-reboot.json).

## Broker and storage evidence

The fourth create passed host convergence. Its public acceptance reported
13 passed and 3 failed. All three failures came from incorrectly requiring
reverse DNS for literal public IPs, which skipped explicit certificate checks.
The run is not a full acceptance pass. The preserved
[output](evidence/2026-09-12/acceptance-attempt4.txt) also used graceful Docker
stop, so its reported zero-second recovery is not abrupt-crash evidence.
The corrected gate verifies IP SANs directly and times an abrupt KILL from
before the fault command.

The [installed storage audit](evidence/2026-09-12/storage-installed.json) found
57 data objects and 9 ops objects outside the package marker prefix. Application
credentials were denied state HEAD/LIST/GET/PUT and native HEAD/PUT with HTTP404;
an independent operator request proved the state bucket and owner object existed.
Native exact-ETag replacement, stale-ETag refusal, lease exclusion, expired
lease takeover, stale release refusal, holder renewal and release all passed.

[Node capture](evidence/2026-09-12/nodes-before-repeat.json) verified three
healthy ARM64 containers, distinct machines, expected node/cluster identities,
a shared secret bundle and CA, private peer connectivity, and the public port
boundary. A separate unique topic contains
[50 exact continuity records](evidence/2026-09-12/continuity-before.json).

## Corrected published acceptance

The installed launcher from package commit
`c3492fd3627091ad3c07a321061689454534fced` pins AutoMQ source
`5aac5a3dfc689071156f5e67c5f1bb7eaec5ca50` and compute source
`58ac766d17cc1b174992986c1088d9d7045e13b5`.
[Build and dry-run passed](evidence/2026-09-12/final-validation.json), and the
fifth full create completed with exit 0 and no local source overrides.

[Public acceptance](evidence/2026-09-12/acceptance-attempt5.txt) passed all 16
gates. It verified TLS against every public IP SAN, three advertised brokers,
200 exact public records, wrong-password and prefix-ACL denial, committed
consumer offsets, and a partition led by the fault victim. An abrupt KILL of
node 2 was timed from before the command: the partition became writable in
11 seconds while the victim remained stopped, and all 100 earlier records
were readable. The broker returned with zero lag and matching log end offset.
Consumer offsets survived, and a restarted controller re-authenticated.

The 20,000-record workload completed at 1,337 records/second with 5,775 ms mean
latency and 11,643 ms p99. These are measurements from the small test cluster,
not a latency or throughput guarantee. The offset check does not force the
`__consumer_offsets` leader to be the failed broker.

The independent [50-record comparison](evidence/2026-09-12/continuity-after-create5.json)
passed after this converge. [Node comparison](evidence/2026-09-12/nodes-after-create5.json)
verified unchanged machine IDs, formatted directory/node/cluster IDs, secret
bundle hashes, CA, server configuration and container image.

The [sixth create](evidence/2026-09-12/acceptance-attempt6.txt) also passed all
16 public gates on the same `5aac5a3` source, with 11-second abrupt recovery.
Its captured [host report](evidence/2026-09-12/host-gates-create6.json) passed
nine gates, including an exact 500-record round trip and all four negative
authentication/authorization checks. Continuity and node identities remained
unchanged after this second full converge.

The repeat reported one firewall change per host. The helper compared the
saved platform rule text with canonical iptables output; an implicit UDP module
became explicit in that output, so the helper appended the same NTP rule.
The [bounded repair](evidence/2026-09-12/firewall-ntp-deduplicated.json) removed
exactly two extra copies per host and preserved the first copy and every other
rule in order. The final helper uses `iptables -C` for semantic existence checks.
AutoMQ source `6b76c01e730c79cfef3e0f9baee68f85b29bd604`, published in
`379608efb87f53a67e5d556c42efd4a660a531c3`, passed
[build and dry-run](evidence/2026-09-12/final-idempotence-validation.json).

The [seventh full create](evidence/2026-09-12/acceptance-attempt7.txt) completed
with exit 0 on `6b76c01`, passing all 16 public gates and
[nine host gates](evidence/2026-09-12/host-gates-create7.json). This time node 1
led the faulted partition; abrupt recovery took 10 seconds and all 100 earlier
records survived. The node returned with zero lag and log end offset 10,133,
consumer offsets remained committed, and controller re-authentication passed.
The workload measured 1,408 records/second, 5,695 ms mean latency and
10,806 ms p99. The firewall task reported unchanged on all three hosts.

The [final independent record comparison](evidence/2026-09-12/continuity-final.json)
returned the exact original 50 records after both host reboots and all three
successful full converges. The [final node comparison](evidence/2026-09-12/nodes-final.json)
passed every check: machine and formatted identities, secrets, CA, configuration
and image remained unchanged, all containers were healthy, private peer ports
were reachable, and only the external Kafka port was publicly reachable.

The [final firewall audit](evidence/2026-09-12/final-firewall.json) applied the
installed helper twice on every node. All six calls reported unchanged; the
complete rule sequence stayed byte-identical and each host retained exactly
one native NTP rule. AutoMQ's firewall service was enabled and active on every
node. Native persistence was enabled everywhere; it was active on rebooted
node 1 and inactive on nodes 0 and 2, where installation deliberately avoided
starting a service that would flush live Docker rules. The earlier repaired
node-1 reboot proves native restoration at boot.

The [final native inventory](evidence/2026-09-12/resources-final.json) found
three running instances, three available boot volumes, exactly the three owned
OCI buckets and one network security group. The shared subnet and VCN remained
available. No cloud mutation or acceptance process was left running.

## Lifecycle scope

The cluster is being retained for use. `compute-prevent-destroy: true` remains
committed, and the [default delete test](evidence/2026-09-12/delete-guard.json)
refused destruction. The current backend credential remains available only in
the ignored private file while the state bucket exists.

Full deletion of this running cluster has not been repeated. The September 11
run proved cleanup of a partial deployment, including application buckets and
identity, the network group, all state versions and the state bucket last,
followed by backend credential revocation. Those results remain historical;
they do not establish deletion of the September 12 broker cluster.
