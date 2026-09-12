import json,subprocess
from pathlib import Path
REMOTE=r'''
import hashlib,json,os,shlex,stat,subprocess
from pathlib import Path
from datetime import datetime,timezone
result={'started_at':datetime.now(timezone.utc).isoformat()}
def run(args,**kwargs):
 p=subprocess.run(args,capture_output=True,text=True,timeout=180,**kwargs)
 if p.returncode:raise RuntimeError('command refused: '+args[0]+' exit '+str(p.returncode))
 return p.stdout
def rules():return run(['iptables-save'])
def normalized(text):return [line for line in text.splitlines() if line.startswith('-A ')]
baseline=Path('/etc/iptables/rules.v4');original=baseline.read_bytes();result['baseline_sha256_before']=hashlib.sha256(original).hexdigest();result['baseline_text']=original.decode();result['rules_before']=rules()
assert b'CLOUD_IMG' in original and b':InstanceServices ' in original
plan=run(['apt-get','-s','install','iptables-persistent'])
removed=[line.split()[1] for line in plan.splitlines() if line.startswith('Remv ')]
assert set(removed)<= {'ufw'},'unexpected apt removal'
result['apt_planned_removals']=removed
policy=Path('/usr/sbin/policy-rc.d');owned=False;policy_body=b'#!/bin/sh\nexit 101\n'
try:
 try:
  fd=os.open(policy,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o755)
 except FileExistsError:
  assert subprocess.run([str(policy),'netfilter-persistent','start'],capture_output=True).returncode==101,'existing policy does not inhibit start'
 else:
  with os.fdopen(fd,'wb') as f:f.write(policy_body)
  owned=True
 run(['debconf-set-selections'],input='iptables-persistent iptables-persistent/autosave_v4 boolean false\niptables-persistent iptables-persistent/autosave_v6 boolean false\n')
 apt=run(['apt-get','-y','-o','Dpkg::Options::=--force-confold','install','iptables-persistent'],env={**os.environ,'DEBIAN_FRONTEND':'noninteractive'})
 result['apt_output_sha256']=hashlib.sha256(apt.encode()).hexdigest()
finally:
 if owned:
  assert policy.read_bytes()==policy_body,'temporary policy changed'
  policy.unlink()
assert baseline.read_bytes()==original,'platform baseline changed during package install'
result['rules_after_install']=rules()
assert normalized(result['rules_before'])==normalized(result['rules_after_install']),'package install changed live rules'
# Reconcile only the authentic image baseline. Existing Docker/AutoMQ chains remain untouched.
lines=original.decode().splitlines();declared=[];saved=[]
for line in lines:
 if line.startswith(':'):
  chain=line.split()[0][1:];assert chain in ('INPUT','FORWARD','OUTPUT','InstanceServices');declared.append(chain)
 if line.startswith('-A '):
  tokens=shlex.split(line);assert tokens[1] in declared;saved.append(tokens)
for chain in declared:
 if chain in ('INPUT','FORWARD','OUTPUT'):continue
 p=subprocess.run(['iptables','--wait','10','-S',chain],capture_output=True,text=True)
 if p.returncode:run(['iptables','--wait','10','-N',chain])
added=[]
for tokens in saved:
 probe=subprocess.run(['iptables','--wait','10','-C',*tokens[1:]],capture_output=True,text=True)
 if probe.returncode:
  run(['iptables','--wait','10',*tokens]);added.append(tokens)
result['restored_rules']=added
result['rules_after_restore']=rules()
# Every preexisting rule must survive byte-for-byte and in original relative order.
old=normalized(result['rules_after_install']);new=normalized(result['rules_after_restore']);iterator=iter(new)
assert all(any(value==prior for value in iterator) for prior in old),'preexisting rule order changed'
run(['systemctl','daemon-reload']);run(['systemctl','enable','netfilter-persistent.service'])
result['persistence_enabled']=run(['systemctl','is-enabled','netfilter-persistent.service']).strip()
result['packages']=run(['dpkg-query','-W','-f','${binary:Package} ${Status}\n','iptables-persistent','netfilter-persistent','ufw'])
result['baseline_sha256_after']=hashlib.sha256(baseline.read_bytes()).hexdigest();result['finished_at']=datetime.now(timezone.utc).isoformat();result['passed']=True
print(json.dumps(result))
'''
records=[]
for i in range(3):
 alias='automq-oci-'+str(i)
 p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',alias,'sudo python3 -'],input=REMOTE,text=True,capture_output=True,timeout=240)
 if p.returncode:
  print(json.dumps({'failed_node':alias,'stderr':p.stderr}));raise SystemExit(1)
 record=json.loads(p.stdout);record['alias']=alias;records.append(record)
 out=Path('/home/ubuntu/code/getcolors/automq-oci/evidence/2026-09-12/platform-persistence-repair.json');out.write_text(json.dumps({'nodes':records},indent=2)+'\n')
 print(json.dumps({'node':alias,'passed':record['passed'],'restored_rule_count':len(record['restored_rules']),'enabled':record['persistence_enabled']}),flush=True)
