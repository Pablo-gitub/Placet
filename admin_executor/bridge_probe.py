"""Operator-only probe 4. Preview by default; never run real restart targets.

Copy this reviewed, digest-pinned standalone file to a protected location first.
No package imports, dependency installation or permanent unit is needed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import grp
import stat
import subprocess

WORKER = 'placet-admin-bridge-probe-worker.service'
RUNNER_UNIT = 'placet-admin-bridge-probe-runner.service'
STAGE = Path('/run/placet-admin-bridge-probe')
UNIT = Path('/run/systemd/system') / WORKER
ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
HELPER = r'''
import json, os, socket, subprocess, time
fields = dict(line.split(':', 1) for line in open('/proc/self/status') if ':' in line)
expected = EXPECTED_GROUPS
assert [int(x) for x in fields['Uid'].split()] == [1000] * 4
assert [int(x) for x in fields['Gid'].split()] == [1000] * 4
assert sorted(int(x) for x in fields['Groups'].split()) == expected
assert int(fields['NoNewPrivs']) == 1
assert all(int(fields[key], 16) == 0 for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
env = {'PATH':'/usr/bin:/bin','LC_ALL':'C','XDG_RUNTIME_DIR':'/run/user/1000',
       'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus'}
def fixed(args):
    p = subprocess.run(['/usr/bin/systemctl','--user',*args], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       timeout=10, env=env, text=True)
    assert len(p.stdout) < 4096
    return p.stdout
before = fixed(['show','placet-probe.service','--property=InvocationID','--value']).strip()
fixed(['restart','placet-probe.service'])
time.sleep(2)
after = fixed(['show','placet-probe.service','--property=InvocationID,ActiveState,SubState'])
state = dict(line.split('=',1) for line in after.splitlines() if '=' in line)
assert state['InvocationID'] and state['InvocationID'] != before
assert state['ActiveState'] == 'active' and state['SubState'] == 'running'
report = json.dumps({'invocation':os.environ['INVOCATION_ID'], 'report':{'state':'succeeded','code':'restart_confirmed'}},separators=(',',':'))
address = os.environ['NOTIFY_SOCKET']
if address.startswith('@'): address = '\0' + address[1:]
with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
    notify.sendto(('STATUS='+report).encode(),address)
'''
RUNNER = r'''
import json, subprocess
unit = 'placet-admin-bridge-probe-worker.service'
def fixed(args):
    p = subprocess.run(['/usr/bin/systemctl','--no-ask-password',*args],check=True,
                       stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                       timeout=40,text=True,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    assert len(p.stdout) < 4096
    return p.stdout
before = fixed(['show',unit,'--property=InvocationID','--value']).strip()
fixed(['start',unit])
raw = fixed(['show',unit,'--property=InvocationID,StatusText,ActiveState,SubState,ExecMainStatus'])
state = dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
assert state['InvocationID'] and state['InvocationID'] != before
assert state['ActiveState']=='active' and state['SubState']=='exited' and state['ExecMainStatus']=='0'
report=json.loads(state['StatusText'])
assert report['invocation']==state['InvocationID']
assert report['report']=={'state':'succeeded','code':'restart_confirmed'}
print(json.dumps({'worker_invocation':state['InvocationID'],'report':report['report']}),flush=True)
'''


def run(argv, timeout=60):
    return subprocess.run(argv, check=True, timeout=timeout, env=ENV,
                          stdin=subprocess.DEVNULL)


def cleanup():
    # --collect can already have unloaded the transient runner. A nonzero
    # stop alone is not an error if both fixed laboratory units are inactive.
    for name in (RUNNER_UNIT,WORKER):
        subprocess.run(['/usr/bin/systemctl','stop',name],check=False,timeout=20,env=ENV,
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        status=subprocess.run(['/usr/bin/systemctl','show',name,'--property=ActiveState','--value'],
                              check=True,capture_output=True,text=True,timeout=10,env=ENV)
        if status.stdout.strip() not in ('inactive','failed'):
            raise ValueError('Laboratory unit still active; retain files for operator inspection')
    UNIT.unlink()
    (STAGE/'helper.py').unlink()
    STAGE.rmdir()
    run(['/usr/bin/systemctl','daemon-reload'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--script-sha256')
    args = parser.parse_args(argv)
    source = Path(__file__).absolute()
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not args.apply:
        print(json.dumps({'mode':'preview','script_sha256':digest,'worker':WORKER,
                          'target':'placet-probe.service','requires':'protected copy, root, exact digest'}))
        return
    if (os.geteuid() != 0 or args.script_sha256 != digest or
            not str(source).startswith('/run/placet-reviewed-probes/')):
        parser.error('Use a root-protected /run/placet-reviewed-probes copy and its exact digest')
    for path in (source, *source.parents):
        info = path.lstat()
        if (info.st_uid or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode)
                or 'system.posix_acl_access' in os.listxattr(path, follow_symlinks=False)):
            raise ValueError('Probe source must be exclusively root-protected')
    if STAGE.exists() or os.path.lexists(UNIT):
        raise ValueError('Existing laboratory paths require manual inspection')
    for name in (WORKER, RUNNER_UNIT):
        check = subprocess.run(['/usr/bin/systemctl','show',name,'--property=LoadState','--value'],
                               check=True, capture_output=True,text=True,timeout=10,env=ENV)
        if check.stdout.strip() != 'not-found':
            raise ValueError('Existing laboratory unit requires manual inspection')
    user = pwd.getpwuid(1000)
    if user.pw_gid != 1000:
        raise ValueError('Probe is reviewed for UID/GID 1000 only')
    groups = sorted(set(os.getgrouplist(user.pw_name, 1000)))
    if {'root','docker','lxd','lxc','disk','libvirt','libvirt-qemu','incus','incus-admin'} & {grp.getgrgid(g).gr_name for g in groups}:
        raise ValueError('Desktop groups require review before this probe')
    STAGE.mkdir(mode=0o755)
    STAGE.chmod(0o755)
    helper = STAGE / 'helper.py'
    helper.write_text(HELPER.replace('EXPECTED_GROUPS',repr(groups)))
    helper.chmod(0o644)
    unit = f'''[Service]
Type=oneshot
RemainAfterExit=yes
User=1000
Group=1000
SupplementaryGroups=
WorkingDirectory=/usr/local/lib/placet-admin
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
ProtectProc=invisible
ProcSubset=pid
RestrictAddressFamilies=AF_UNIX
InaccessiblePaths=-/home -/root -/var/lib/placet -/var/lib/placet-admin -/var/lib/placet-admin-web -/var/lib/placet-admin-tailscale
NotifyAccess=main
Restart=no
TimeoutStartSec=35
TimeoutStopSec=5
KillMode=control-group
StandardInput=null
StandardOutput=null
StandardError=null
ExecStart=/usr/local/lib/placet-admin-venv/bin/python -I {helper}
'''
    UNIT.write_text(unit)
    UNIT.chmod(0o644)
    try:
        run(['/usr/bin/systemctl','daemon-reload'])
        properties = ['WorkingDirectory=/usr/local/lib/placet-admin','UMask=0077',
                      'NoNewPrivileges=yes','CapabilityBoundingSet=CAP_SETUID CAP_SETGID',
                      'ProtectSystem=strict','ProtectHome=read-only','PrivateTmp=yes','PrivateDevices=yes',
                      'ProtectKernelTunables=yes','ProtectKernelModules=yes','ProtectControlGroups=yes',
                      'RestrictSUIDSGID=yes','RestrictNamespaces=yes','ProtectProc=invisible','ProcSubset=pid',
                      'RestrictAddressFamilies=AF_UNIX AF_INET','MemoryMax=256M','TasksMax=32',
                      'ReadWritePaths=/var/lib/placet-admin',
                      'InaccessiblePaths=-/home -/root -/var/lib/placet -/var/lib/placet-admin-web -/var/lib/placet-admin-tailscale',
                      'KillMode=control-group','TimeoutStopSec=10','RuntimeMaxSec=50','Restart=no']
        run(['/usr/bin/systemd-run','--unit='+RUNNER_UNIT,'--collect','--wait','--pipe','--uid=0','--gid=0',
             *['--property='+p for p in properties],'/usr/bin/env','-i','PATH=/usr/bin:/bin',
             '/usr/local/lib/placet-admin-venv/bin/python','-I','-c',RUNNER])
    finally:
        # Only fixed laboratory units are stopped, never production services.
        cleanup()


if __name__ == '__main__':
    main()
