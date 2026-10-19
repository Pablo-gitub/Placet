"""Fail-closed host prerequisites; never change accounts, groups or containers."""
import grp
import os
from pathlib import Path
import pwd
import stat

ROOT_GROUPS = frozenset({'root', 'docker', 'lxd', 'lxc', 'disk'})
UNASSESSED = frozenset({'libvirt', 'libvirt-qemu', 'incus', 'incus-admin'})


def assess(*, desktop_groups, process_groups, peer_group_members, privileged_sockets, unassessed_groups):
    problems = []
    if ROOT_GROUPS & (set(desktop_groups) | set(process_groups)):
        problems.append('Desktop account or a running process retains root-equivalent groups')
    if peer_group_members:
        problems.append('Administrative peer groups must have no supplementary members')
    if privileged_sockets:
        problems.append('Desktop identity can access a privileged daemon socket')
    if unassessed_groups:
        problems.append('Virtualization administration groups require a separate reviewed policy')
    return problems


def account(name):
    user = pwd.getpwnam(name)
    if user.pw_uid <= 0 or user.pw_gid <= 0:
        raise ValueError('A distinct unprivileged account is required')
    return user


def inspect_host(desktop_name):
    desktop, web, backend = (account(name) for name in (desktop_name, 'placet-admin-web', 'placet'))
    if len({desktop.pw_uid, web.pw_uid, backend.pw_uid}) != 3:
        raise ValueError('Desktop, web and backend identities must be distinct')
    for peer in (web, backend):
        if (peer.pw_shell != '/usr/sbin/nologin'
                or set(os.getgrouplist(peer.pw_name, peer.pw_gid)) != {peer.pw_gid}):
            raise ValueError('Administrative peers require non-login accounts without extra groups')
    peer_groups = {web.pw_gid, backend.pw_gid}
    if desktop.pw_gid in peer_groups or web.pw_gid == backend.pw_gid:
        raise ValueError('Administrative socket groups must be distinct')
    desktop_gids = set(os.getgrouplist(desktop.pw_name, desktop.pw_gid))
    memberships = set()
    for gid in peer_groups:
        memberships.update(grp.getgrgid(gid).gr_mem)
        memberships.update(u.pw_name for u in pwd.getpwall() if u.pw_gid == gid and u.pw_uid not in {web.pw_uid, backend.pw_uid})
    if desktop_gids & peer_groups:
        memberships.add(desktop.pw_name)
    process_gids = set()
    # /proc must be completely observable as root. A vanished process is normal;
    # permission/parsing failures are not evidence that the account is safe.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = dict(line.split(':', 1) for line in (entry / 'status').read_text().splitlines() if ':' in line)
            uids = {int(n) for n in fields['Uid'].split()}
            gids = {int(n) for n in (fields['Groups'] + ' ' + fields['Gid']).split()}
            if desktop.pw_uid in uids:
                process_gids.update(gids)
            for peer in (web, backend):
                if peer.pw_uid in uids and (gids != {peer.pw_gid} or 0 in uids):
                    raise ValueError('A running administrative peer retains unexpected privileges')
        except (FileNotFoundError, ProcessLookupError):
            continue
    if process_gids & peer_groups:
        memberships.add(desktop.pw_name)
    names = lambda gids: {grp.getgrgid(gid).gr_name for gid in gids}
    sockets = []
    # Effective DAC access, including groups retained by existing processes.
    for path in ('/var/run/docker.sock', '/var/lib/lxd/unix.socket', '/var/lib/incus/unix.socket',
                 '/run/libvirt/libvirt-sock', '/run/containerd/containerd.sock'):
        try:
            info = Path(path).stat()
        except FileNotFoundError:
            continue
        if not stat.S_ISSOCK(info.st_mode):
            raise ValueError('Unexpected privileged daemon path')
        mode = info.st_mode
        if ((info.st_uid == desktop.pw_uid and mode & 0o200)
                or (info.st_gid in desktop_gids | process_gids and mode & 0o020) or mode & 0o002):
            sockets.append(path)
        # Extended ACLs could grant access beyond mode bits; fail closed rather
        # than infer safety from an incomplete permission model.
        if 'system.posix_acl_access' in os.listxattr(path):
            sockets.append(path + ' (ACL requires review)')
    desktop_names, process_names = names(desktop_gids), names(process_gids)
    problems = assess(desktop_groups=desktop_names, process_groups=process_names,
                      peer_group_members=memberships, privileged_sockets=sockets,
                      unassessed_groups=UNASSESSED & (desktop_names | process_names))
    if problems:
        raise ValueError('; '.join(problems))
    return desktop, web, backend
