from pathlib import Path
import json,subprocess
REMOTE=r'''
import hashlib,json,shlex,subprocess
from datetime import datetime,timezone
from pathlib import Path
def run(args, checked=True):
 p=subprocess.run(args,capture_output=True,text=True,timeout=60)
 if checked and p.returncode:raise RuntimeError('firewall audit command failed')
 return p.stdout
helper='/usr/local/lib/automq-firewall.py';config='/etc/automq/firewall.json'
before=run(['iptables','-S'])
first=json.loads(run(['/usr/bin/python3',helper,config]));middle=run(['iptables','-S'])
second=json.loads(run(['/usr/bin/python3',helper,config]));after=run(['iptables','-S'])
ntp=[]
for line in after.splitlines():
 tokens=shlex.split(line)
 if tokens[:2]==['-A','InstanceServices'] and '--dport' in tokens and tokens[tokens.index('--dport')+1]=='123':ntp.append(line)
services={name:{'enabled':run(['systemctl','is-enabled',name]).strip(),'active':run(['systemctl','is-active',name],checked=False).strip()} for name in ['netfilter-persistent.service','automq-firewall.service']}
checks={'first_apply_unchanged':first['changed'] is False,'second_apply_unchanged':second['changed'] is False,'rules_byte_identical':before==middle==after,'ntp_123_exactly_one':len(ntp)==1,'native_persistence_enabled':services['netfilter-persistent.service']['enabled']=='enabled','owned_firewall_enabled_active':services['automq-firewall.service']=={'enabled':'enabled','active':'active'}}
print(json.dumps({'observed_at':datetime.now(timezone.utc).isoformat(),'helper_sha256':hashlib.sha256(Path(helper).read_bytes()).hexdigest(),'platform_baseline_sha256':hashlib.sha256(Path('/etc/iptables/rules.v4').read_bytes()).hexdigest(),'first_apply':first,'second_apply':second,'before_sha256':hashlib.sha256(before.encode()).hexdigest(),'middle_sha256':hashlib.sha256(middle.encode()).hexdigest(),'after_sha256':hashlib.sha256(after.encode()).hexdigest(),'ntp_123_rules':ntp,'services':services,'checks':checks,'passed':all(checks.values())}))
'''
results=[]
for i in range(3):
 alias='automq-oci-'+str(i)
 p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',alias,'sudo python3 -'],input=REMOTE,capture_output=True,text=True,timeout=180)
 if p.returncode:raise RuntimeError('node firewall audit failed: '+alias)
 result=json.loads(p.stdout);result['alias']=alias;results.append(result)
 print(json.dumps({'node':alias,'passed':result['passed']}),flush=True)
out=Path('/home/ubuntu/code/getcolors/automq-oci/evidence/2026-09-12/final-firewall.json');out.write_text(json.dumps({'nodes':results,'passed':all(x['passed'] for x in results)},indent=2)+'\n')
raise SystemExit(0 if all(x['passed'] for x in results) else 1)
