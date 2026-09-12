#!/usr/bin/env python3
"""Reboot only node 1 after acceptance, then verify firewall and broker recovery."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time

FACTS = r'''
import json,subprocess
from pathlib import Path
def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=20)
    return p.stdout.strip()
rules=run(['iptables-save'])
props=dict(line.split('=',1) for line in Path('/etc/automq/server.properties').read_text().splitlines() if '=' in line and not line.startswith('#'))
listeners=dict(item.split('://',1) for item in props['listeners'].split(','))
container=run(['docker','inspect','--format','{{json .State}}','automq'])
try:state=json.loads(container)
except ValueError:state={}
print(json.dumps({'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'instance_id':Path('/var/lib/cloud/data/instance-id').read_text().strip(),'firewall_enabled':run(['systemctl','is-enabled','automq-firewall.service']),'firewall_active':run(['systemctl','is-active','automq-firewall.service']),'input_rules':[line for line in rules.splitlines() if line.startswith('-A INPUT ')],'owned_rules':[line for line in rules.splitlines() if line.startswith('-A AUTOMQ_')],'platform_rules':sorted(line for line in rules.splitlines() if line.startswith('-A InstanceServices ') or (line.startswith('-A OUTPUT ') and 'InstanceServices' in line) or (line.startswith(('-A INPUT ','-A FORWARD ')) and 'REJECT --reject-with icmp-host-prohibited' in line)),'container_status':state.get('Status'),'container_health':state.get('Health',{}).get('Status'),'private_host':listeners['INTERNAL'].rsplit(':',1)[0],'private_ports':[int(listeners[role].rsplit(':',1)[1]) for role in ('CONTROLLER','INTERNAL')]}))
'''
TCP = r'''
import json,socket,sys
x=json.loads(sys.argv[1]);result=[]
for port in x['private_ports']:
    try:
        with socket.create_connection((x['private_host'],port),timeout=5):opened=True
    except OSError:opened=False
    result.append({'port':port,'open':opened})
print(json.dumps(result))
'''
def remote(alias,script,argument=None):
    command='sudo python3 -'+(' '+shlex.quote(json.dumps(argument)) if argument is not None else '')
    p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2',alias,command],input=script,text=True,capture_output=True,timeout=40)
    if p.returncode:raise RuntimeError('SSH observation unavailable')
    return json.loads(p.stdout)
def now():return datetime.now(timezone.utc).isoformat()
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',default='automq-oci')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--execute',action='store_true',help='Required explicit permission to reboot node 1 now')
    args=parser.parse_args()
    if not args.execute:parser.error('--execute required; no host operations performed')
    alias=args.profile+'-1';result={'profile':args.profile,'node':alias,'started_at':now(),'passed':False}
    try:
        before=remote(alias,FACTS);result['before']=before
        chain='AUTOMQ_'+hashlib.sha256(args.profile.encode()).hexdigest()[:16].upper()
        assert before['container_status']=='running' and before['container_health']=='healthy','broker unhealthy before reboot'
        assert before['firewall_enabled']=='enabled' and before['firewall_active']=='active','firewall not active before reboot'
        result['reboot_requested_at']=now()
        request=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',alias,'sudo -n systemctl reboot'],capture_output=True,text=True,timeout=20)
        result['reboot_command_exit']=request.returncode
        assert request.returncode in (0,255),'reboot command failed'
        deadline=time.monotonic()+600;after=None
        while time.monotonic()<deadline:
            time.sleep(5)
            try:after=remote(alias,FACTS)
            except (RuntimeError,ValueError,subprocess.TimeoutExpired):continue
            if after['boot_id']!=before['boot_id'] and after['container_status']=='running' and after['container_health']=='healthy':break
        result['after']=after
        assert after is not None,'node did not return within 600 seconds'
        peers={args.profile+'-'+str(i):remote(args.profile+'-'+str(i),TCP,after) for i in (0,2)}
        result['peer_tcp']=peers
        result['checks']={'boot_id_changed':before['boot_id']!=after['boot_id'],'same_instance':before['instance_id']==after['instance_id'],'firewall_enabled_active':after['firewall_enabled']=='enabled' and after['firewall_active']=='active','first_input_jump_owned':bool(after['input_rules']) and ('-j '+chain) in after['input_rules'][0],'owned_rules_preserved':before['owned_rules']==after['owned_rules'],'platform_rules_preserved':before['platform_rules']==after['platform_rules'],'broker_healthy':after['container_status']=='running' and after['container_health']=='healthy','private_ports_from_peers':all(row['open'] for rows in peers.values() for row in rows)}
        result['passed']=all(result['checks'].values())
    except (AssertionError,RuntimeError,ValueError,OSError,subprocess.TimeoutExpired) as error:
        result['error']=str(error)
    finally:
        result['finished_at']=now();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':result['passed'],'output':str(args.output)}))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
