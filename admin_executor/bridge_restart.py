"""Unprivileged fixed system worker. Its result is desktop-user evidence only."""
import grp
import json
import os
from pathlib import Path
import socket
import stat
import sys

POLICY = Path('/usr/local/lib/placet-admin/bridge-worker.json')
CAPS = ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')
FORBIDDEN = frozenset({'root','docker','lxd','lxc','disk','libvirt','libvirt-qemu','incus','incus-admin'})


def verify_identity(policy, fields, *, group_name=lambda gid: grp.getgrgid(gid).gr_name):
    if (not isinstance(policy,dict) or set(policy) != {'uid','gid','groups'}
            or any(type(policy[k]) is not int or policy[k] <= 0 for k in ('uid','gid'))
            or not isinstance(policy['groups'],list) or not policy['groups']
            or any(type(g) is not int or g <= 0 for g in policy['groups'])
            or policy['groups'] != sorted(set(policy['groups']))):
        raise ValueError('Invalid protected worker identity')
    if (list(map(int,fields['Uid'].split())) != [policy['uid']]*4
            or list(map(int,fields['Gid'].split())) != [policy['gid']]*4
            or sorted(map(int,fields['Groups'].split())) != policy['groups']
            or int(fields['NoNewPrivs']) != 1
            or any(int(fields[key],16) != 0 for key in CAPS)
            or any(group_name(g) in FORBIDDEN for g in policy['groups'])):
        raise ValueError('Worker privilege invariants failed')


def read_policy():
    for path in (POLICY, *POLICY.parents):
        info = path.lstat()
        if (info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode)
                or 'system.posix_acl_access' in os.listxattr(path,follow_symlinks=False)):
            raise ValueError('Unprotected worker policy')
    fd = os.open(POLICY,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError('Invalid worker policy file')
        data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError('Worker policy exceeds limit')
    return json.loads(data)


def notify(report):
    invocation = os.environ.get('INVOCATION_ID','')
    address = os.environ.get('NOTIFY_SOCKET','')
    if (len(invocation)!=32 or any(c not in '0123456789abcdef' for c in invocation)
            or not address or len(address)>108 or not address.startswith(('/', '@'))):
        raise ValueError('System-manager notification context required')
    envelope = json.dumps({'invocation':invocation,'report':json.loads(report)},separators=(',',':'))
    if len(envelope)>1200:
        raise ValueError('Worker notification exceeds limit')
    if address.startswith('@'):
        address='\0'+address[1:]
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as channel:
        channel.settimeout(2)
        channel.sendto(('STATUS='+envelope).encode(),address)


def main():
    if os.geteuid() == 0 or len(sys.argv) != 1:
        raise ValueError('Unprivileged fixed worker required')
    policy = read_policy()
    with open('/proc/self/status',encoding='ascii') as stream:
        fields=dict(line.split(':',1) for line in stream if ':' in line)
    verify_identity(policy,fields)
    # This script is installed in a root-owned package; isolated Python excludes
    # the checkout, environment and user site before adding its protected sibling.
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    try:
        from restart import BRIDGE, restart_service
    except Exception:
        notify(json.dumps({'state':'failed','code':'helper_import_failed'}))
        return
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C','XDG_RUNTIME_DIR':f'/run/user/{policy["uid"]}',
         'DBUS_SESSION_BUS_ADDRESS':f'unix:path=/run/user/{policy["uid"]}/bus'}
    notify(restart_service(BRIDGE,user=True,env=env).encode_report())


if __name__ == '__main__':
    main()
