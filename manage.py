#!/usr/bin/env python3
"""Build/install/control the per-user service. Installation starts paused."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
DEST = Path.home() / 'Library/Application Support/UUBetterDisplaySwitch'
LABEL = 'io.github.uu-betterdisplay-switch'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
DOMAIN = 'gui/' + str(os.getuid())

def call(*args, **kwargs):
    return subprocess.run(list(args), check=True, **kwargs)

def build(folder):
    folder.mkdir(parents=True, exist_ok=True)
    app = folder / 'UU Display Switch.app'
    executable = app / 'Contents/MacOS/session-probe'
    executable.parent.mkdir(parents=True, exist_ok=True)
    info = {'CFBundleIdentifier': LABEL + '.probe', 'CFBundleName': 'UU Display Switch',
            'CFBundleExecutable': 'session-probe', 'CFBundlePackageType': 'APPL',
            'CFBundleVersion': '1', 'LSUIElement': True}
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    for source, target in [('session-probe', executable), ('input-events', folder/'input-events'),
                           ('display-sleep', folder/'display-sleep')]:
        call('/usr/bin/xcrun', 'swiftc', str(SOURCE/(source+'.swift')), '-o', str(target))
    call('/usr/bin/codesign', '--force', '--sign', '-', str(app))

def installed(action):
    call(sys.executable, str(DEST/'switcher.py'), action)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'install', 'status', 'resume', 'restore', 'restart', 'uninstall'])
    parser.add_argument('--config', type=Path, default=SOURCE/'config.json')
    args = parser.parse_args()
    if args.action == 'build':
        build(SOURCE/'build')
    elif args.action == 'install':
        if DEST.exists() or PLIST.exists():
            raise SystemExit('Already installed. Restore and uninstall first; keep your config backup.')
        import switcher
        switcher.load_config(args.config)
        for name in ['/Applications/BetterDisplay.app', '/Applications/UURemote.app']:
            if not Path(name).exists(): raise SystemExit('Missing application: ' + name)
        # Read-only checks before installing; do not disconnect anything here.
        bd = switcher.BetterDisplay()
        for identity in switcher.PHYSICAL:
            if not bd.get('UUID='+identity): raise SystemExit('Connect every configured physical display first')
        bd.get(switcher.VIRTUAL)
        build(SOURCE/'build')
        shutil.copytree(SOURCE/'build', DEST)
        shutil.copy2(SOURCE/'switcher.py', DEST/'switcher.py')
        shutil.copy2(args.config, DEST/'config.json')
        (DEST/'config.json').chmod(0o600)
        state = DEST/'state'
        state.mkdir(mode=0o700)
        (state/'paused').touch()
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(plistlib.dumps({'Label':LABEL,
            'ProgramArguments':[sys.executable, str(DEST/'switcher.py'), '--daemon'],
            'RunAtLoad':True, 'KeepAlive':True, 'ThrottleInterval':10,
            'ProcessType':'Interactive', 'StandardOutPath':str(state/'launchd.log'),
            'StandardErrorPath':str(state/'launchd.log')}))
        for name, action in [('Restore and Pause','--restore'),('Enable','--resume'),('Status','--status')]:
            path = DEST/(name+'.command')
            path.write_text('#!/bin/sh\n'+shlex.join([sys.executable,str(DEST/'switcher.py'),action])+'\n')
            path.chmod(0o755)
        call('/bin/launchctl','bootstrap',DOMAIN,str(PLIST))
        print('Installed PAUSED. Grant permissions, restart, check status, then resume.\n'+str(DEST))
    elif args.action == 'restart':
        call('/bin/launchctl','kill','SIGTERM',DOMAIN+'/'+LABEL)
        print('launchd will restart the service; wait about 10 seconds before checking status.')
    elif args.action == 'uninstall':
        # Do not remove the recovery tools if restoring physical screens fails.
        installed('--restore')
        call('/bin/launchctl','bootout',DOMAIN,str(PLIST))
        PLIST.unlink()
        print('Service removed. Recovery tools and local config remain at '+str(DEST))
        print('After confirming your screens work, delete that folder manually if desired.')
    else:
        installed('--'+args.action)

if __name__ == '__main__': main()
