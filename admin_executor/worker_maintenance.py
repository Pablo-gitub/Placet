"""Pinned, operator-only maintenance for the fixed bridge worker.

Preview never runs a command. Apply only from a protected standalone copy;
checkout code is read as data, never imported or executed by this installer.
Only the worker unit is installed/replaced. Existing admin units are unchanged.
"""
import argparse
import ast
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import runpy
import sqlite3
import stat
import subprocess

PREFIX=Path('/usr/local/lib/placet-admin')
PACKAGE=PREFIX/'admin_executor'
CONFIG=Path('/etc/placet-admin')
STATE=Path('/var/lib/placet-admin')
UNITS=Path('/etc/systemd/system')
WORKER='placet-admin-bridge-worker.service'
POLICY=PREFIX/'bridge-worker.json'
SERVICES=('placet-admin-web.service','placet-admin-control.service','placet-admin-supervisor.service')
ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C'}
CHANGES=frozenset({'bootstrap.py','restart.py','execution.py','bridge_restart.py','bridge_worker.py',
                   'worker_units.py','worker_maintenance.py','web_static/en.json','web_static/it.json',
                   'web_static/app.js','web_static/index.html'})
FORBIDDEN=frozenset({'root','docker','lxd','lxc','disk','libvirt','libvirt-qemu','incus','incus-admin'})


def read(path,limit=1048576):
    fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
            raise ValueError('Bounded regular file required')
        data=stream.read(limit+1)
        if len(data)>limit: raise ValueError('File exceeds limit')
    return data


def snapshot(source):
    bootstrap=read(source/'bootstrap.py')
    nodes=[n for n in ast.parse(bootstrap).body if isinstance(n,ast.Assign)
           and any(isinstance(t,ast.Name) and t.id=='MODULES' for t in n.targets)]
    if len(nodes)!=1: raise ValueError('One literal manifest required')
    names=ast.literal_eval(nodes[0].value)
    if not isinstance(names,tuple) or len(names)>100 or len(set(names))!=len(names):
        raise ValueError('Invalid package manifest')
    files={}; digest=hashlib.sha256()
    for name in names:
        if (not isinstance(name,str) or name.startswith('/') or '\\' in name
                or any(p in ('','.','..') for p in name.split('/'))):
            raise ValueError('Invalid manifest path')
        for parent in (source/name).parents:
            if parent==source.parent: break
            if parent.is_symlink() or not parent.is_dir(): raise ValueError('Source directory link refused')
        data=bootstrap if name=='bootstrap.py' else read(source/name)
        files[name]=data
        digest.update(name.encode()+b'\0'+len(data).to_bytes(8,'big')+data)
    return files,digest.hexdigest()


def protected(path,*,directory=False,private=False):
    for item in (path,*path.parents):
        info=item.lstat()
        is_dir=item!=path or directory
        if (info.st_uid!=0 or info.st_mode & (0o077 if private and item==path else 0o022)
                or not (stat.S_ISDIR(info.st_mode) if is_dir else stat.S_ISREG(info.st_mode))
                or 'system.posix_acl_access' in os.listxattr(item,follow_symlinks=False)
                or (not is_dir and info.st_nlink!=1)):
            raise ValueError('Exclusively root-protected installation required: '+str(item))


def parse_groups(raw):
    if not isinstance(raw,str) or not raw: raise ValueError('Explicit sorted group IDs required')
    try: values=[int(n) for n in raw.split(',')]
    except ValueError: raise ValueError('Group IDs must be numeric') from None
    if values!=sorted(set(values)) or any(n<=0 for n in values):
        raise ValueError('Group IDs must be positive, sorted and unique')
    return values


def render_unit(source,uid,gid):
    """Read a literal f-string as data; never execute the checkout unit module."""
    if any(type(n) is not int or n<=0 for n in (uid,gid)): raise ValueError('Non-root identity required')
    tree=ast.parse(source)
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='worker_unit']
    if len(functions)!=1 or not isinstance(functions[0].body[-1],ast.Return):
        raise ValueError('Fixed unit template required')
    template=functions[0].body[-1].value
    if not isinstance(template,ast.JoinedStr): raise ValueError('Literal unit template required')
    pieces=[]
    for node in template.values:
        if isinstance(node,ast.Constant) and type(node.value) is str: pieces.append(node.value)
        elif (isinstance(node,ast.FormattedValue) and isinstance(node.value,ast.Name)
              and node.value.id in ('uid','gid') and node.conversion==-1 and node.format_spec is None):
            pieces.append(str(uid if node.value.id=='uid' else gid))
        else: raise ValueError('Executable unit interpolation refused')
    return ''.join(pieces)


def validate_changes(old,new):
    if set(old)-set(new): raise ValueError('Maintenance cannot remove package modules')
    if {name for name in new if old.get(name)!=new[name]}-CHANGES:
        raise ValueError('Changes outside the worker maintenance scope require separate review')


def check_jobs(path):
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        if db.execute("SELECT 1 FROM execution_jobs WHERE state IN ('running','outcome_unknown') LIMIT 1").fetchone():
            raise ValueError('Resolve running or uncertain jobs before maintenance')
        if db.execute("""SELECT 1 FROM authorized_jobs a WHERE a.state='authorized' AND NOT EXISTS
            (SELECT 1 FROM execution_jobs e WHERE e.job_id=a.id) LIMIT 1""").fetchone():
            raise ValueError('Pending authorizations must finish before maintenance')


def worker_quiescent(fields,database):
    if fields.get('Job'): return False
    if fields.get('LoadState')=='not-found': return True
    if fields.get('ActiveState')=='inactive': return True
    if fields.get('SubState') not in ('exited','failed','dead'): return False
    try:
        with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as db:
            return db.execute('''SELECT 1 FROM execution_workers w JOIN execution_jobs e ON e.job_id=w.job_id
              WHERE w.invocation=? AND e.state IN ('succeeded','failed')''',
              (fields.get('InvocationID'),)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def command(*argv):
    # All call sites provide fixed executables, verbs, units and property names.
    return subprocess.run(list(argv),check=True,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,env=ENV,timeout=40,text=True)


def worker_status():
    raw=command('/usr/bin/systemctl','show',WORKER,'--property=LoadState,ActiveState,SubState,InvocationID,Job').stdout
    if len(raw)>4096: raise ValueError('Invalid worker status')
    return dict(line.split('=',1) for line in raw.splitlines() if '=' in line)


def write_new(path,data,mode=0o600):
    fd=os.open(path,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data); stream.flush(); os.fchmod(stream.fileno(),mode); os.fsync(stream.fileno())


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def replace(path,data,mode):
    stage=path.with_name(path.name+'.worker-new')
    write_new(stage,data,mode); stage.replace(path); sync_directory(path.parent)


def inspect_host(identity,groups):
    user=pwd.getpwuid(identity['uid'])
    if user.pw_gid!=identity['gid'] or sorted(set(os.getgrouplist(user.pw_name,user.pw_gid)))!=groups:
        raise ValueError('Explicit groups differ from current desktop identity')
    if FORBIDDEN & {grp.getgrgid(g).gr_name for g in groups}:
        raise ValueError('Root-equivalent or unassessed desktop groups refused')
    # Execute only the already-installed root-protected host inspector, never
    # its checkout replacement. Its package digest has already been pinned.
    checks=runpy.run_path(str(PACKAGE/'activation_checks.py'))
    checks['inspect_host'](user.pw_name)
    policy=command('/usr/bin/sudo','-l','-U',user.pw_name).stdout
    if len(policy)>65536 or any(s in policy for s in ('NOPASSWD:','!authenticate','exempt_group=','timestamp_type=global')):
        raise ValueError('Unattended desktop elevation requires review')


def apply(files,args,unit,identity,groups):
    for path in (PREFIX,PACKAGE,CONFIG,STATE,UNITS):
        protected(path,directory=True,private=path in (CONFIG,STATE))
    database=STATE/'authorization.sqlite'
    protected(database,private=True)
    for name in ('desktop-identity.json','catalogue.json','origin','peers.json','health.json','return-url'):
        protected(CONFIG/name)
    for service in (*SERVICES,WORKER):
        for base in (Path('/etc/systemd/system'),Path('/run/systemd/system')):
            if (base/(service+'.d')).exists(): raise ValueError('Unit drop-ins require separate review')
        if service!=WORKER: protected(UNITS/service)
    if os.path.lexists(Path('/run/systemd/system')/WORKER):
        raise ValueError('Runtime worker override requires review')
    old,digest=snapshot(PACKAGE)
    if digest!=args.installed_sha256: raise ValueError('Installed bundle changed')
    for name in old: protected(PACKAGE/name)
    validate_changes(old,files)
    if json.loads(read(CONFIG/'desktop-identity.json',4096))!=identity:
        raise ValueError('Desktop identity changed during preflight')
    if os.path.lexists(UNITS/WORKER):
        protected(UNITS/WORKER)
        if ('worker_units.py' not in old or read(UNITS/WORKER)!=render_unit(old['worker_units.py'],identity['uid'],identity['gid']).encode()):
            raise ValueError('Existing worker unit differs from its installed template')
    if os.path.lexists(POLICY): protected(POLICY)
    inspect_host(identity,groups)
    check_jobs(database)
    if not worker_quiescent(worker_status(),database): raise ValueError('Worker is not quiescent')
    command('/usr/bin/systemctl','is-active','--quiet',*SERVICES)
    stage=PREFIX/'admin_executor.worker-stage'
    backup=PREFIX/('admin_executor.before-worker-'+digest)
    evidence=STATE/('worker-maintenance-'+digest)
    for path in (stage,backup,evidence,POLICY.with_name(POLICY.name+'.worker-new'),
                 (UNITS/WORKER).with_name(WORKER+'.worker-new')):
        if os.path.lexists(path): raise ValueError('Interrupted or repeated maintenance requires review: '+str(path))
    lock=os.open(database.with_suffix('.execution.lock'),os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,0o600)
    frozen=False; changed=False
    try:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Freeze all authorization writers, including in-flight WebAuthn calls,
        # while the same lock prevents the supervisor from dispatching a job.
        frozen=True
        command('/usr/bin/systemctl','stop',*SERVICES)
        if snapshot(PACKAGE)[1]!=digest: raise ValueError('Installed package changed after freeze')
        check_jobs(database)
        fields=worker_status()
        if not worker_quiescent(fields,database): raise ValueError('Worker changed after freeze')
        # Read-only guards passed. From here failures leave admin services stopped
        # for explicit recovery, rather than running a mixed package automatically.
        changed=True
        evidence.mkdir(mode=0o700)
        write_new(evidence/'authorization.sqlite',b'')
        with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as incoming, sqlite3.connect(evidence/'authorization.sqlite') as out:
            incoming.backup(out)
        write_new(evidence/'catalogue.json',read(CONFIG/'catalogue.json'))
        if POLICY.exists(): write_new(evidence/'bridge-worker.json',read(POLICY))
        if (UNITS/WORKER).exists(): write_new(evidence/WORKER,read(UNITS/WORKER))
        write_new(evidence/'record.json',json.dumps({'installed_sha256':digest,'new_sha256':args.bundle_sha256,
                                                   'state':'frozen','groups':groups}).encode())
        if fields.get('LoadState')!='not-found':
            command('/usr/bin/systemctl','stop',WORKER)
        stage.mkdir(mode=0o755); stage.chmod(0o755)
        for name,data in files.items():
            target=stage/name; target.parent.mkdir(parents=True,exist_ok=True,mode=0o755); target.parent.chmod(0o755)
            write_new(target,data,0o644)
        for directory, _, _ in os.walk(stage,topdown=False):
            sync_directory(Path(directory))
        PACKAGE.rename(backup); stage.rename(PACKAGE); sync_directory(PREFIX)
        replace(POLICY,json.dumps({'uid':identity['uid'],'gid':identity['gid'],'groups':groups},sort_keys=True).encode()+b'\n',0o644)
        replace(UNITS/WORKER,unit.encode(),0o644)
        command('/usr/bin/systemctl','daemon-reload')
        # Never enable/start the worker, backend or bridge as an installer test.
        command('/usr/bin/systemctl','start','placet-admin-control.service','placet-admin-web.service','placet-admin-supervisor.service')
        command('/usr/bin/systemctl','is-active','--quiet',*SERVICES)
        replace(evidence/'record.json',json.dumps({'installed_sha256':digest,'new_sha256':args.bundle_sha256,
                                                 'state':'installed','groups':groups}).encode(),0o600)
        sync_directory(evidence)
        print('Fixed worker installed. No restart job authorized or executed. Passkeys, jobs and audit preserved.')
    except BaseException:
        if frozen and not changed:
            command('/usr/bin/systemctl','start',*SERVICES)
        # Once changed, leave stopped on failure; never roll back an auth DB.
        elif changed:
            command('/usr/bin/systemctl','stop',*SERVICES)
        raise
    finally:
        os.close(lock)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--apply',action='store_true')
    for name in ('installer-sha256','bundle-sha256','installed-sha256','unit-sha256','expected-groups'):
        parser.add_argument('--'+name)
    args=parser.parse_args(argv)
    files,digest=snapshot(args.source)
    if not args.apply:
        identity=None; installed=None; unit_digest=None; expected=None
        try:
            identity=json.loads(read(CONFIG/'desktop-identity.json',4096))
            installed=snapshot(PACKAGE)[1]
            user=pwd.getpwuid(identity['uid']); expected=sorted(set(os.getgrouplist(user.pw_name,identity['gid'])))
            unit_digest=hashlib.sha256(render_unit(files['worker_units.py'],identity['uid'],identity['gid']).encode()).hexdigest()
        except (FileNotFoundError,PermissionError): pass
        print(json.dumps({'mode':'preview','installer_sha256':hashlib.sha256(read(Path(__file__))).hexdigest(),
                          'bundle_sha256':digest,'installed_sha256':installed,'unit_sha256':unit_digest,
                          'identity':identity,'expected_groups':expected,'services_restarted':[],
                          'requires':'protected installer copy, root, four digests, explicit groups; no pending/uncertain jobs'},indent=2))
        return
    pins=(args.installer_sha256,args.bundle_sha256,args.installed_sha256,args.unit_sha256)
    if (os.geteuid()!=0 or any(not p or len(p)!=64 or any(c not in '0123456789abcdef' for c in p) for p in pins)
            or args.bundle_sha256!=digest or not args.expected_groups):
        parser.error('Root, four exact reviewed digests and explicit groups are required')
    own=Path(__file__).absolute()
    protected(own)
    if args.installer_sha256!=hashlib.sha256(read(own)).hexdigest():
        parser.error('Protected installer digest differs from review')
    protected(CONFIG/'desktop-identity.json')
    identity=json.loads(read(CONFIG/'desktop-identity.json',4096))
    if (not isinstance(identity,dict) or set(identity)!={'uid','gid'}
            or any(type(identity[k]) is not int or identity[k]<=0 for k in ('uid','gid'))):
        raise ValueError('Protected desktop identity required')
    groups=parse_groups(args.expected_groups)
    unit=render_unit(files['worker_units.py'],identity['uid'],identity['gid'])
    if hashlib.sha256(unit.encode()).hexdigest()!=args.unit_sha256: parser.error('Worker unit digest differs from review')
    os.umask(0o077)
    apply(files,args,unit,identity,groups)


if __name__=='__main__':
    main()
