'use strict';

// Read command lines and open-file paths, never process environments. Filtering
// before returning keeps large unrelated process lists out of the capped reader.
const ENVIRONMENT_USAGE = String.raw`
import json, re, subprocess, sys
root = sys.argv[1]
processes = subprocess.run(['/bin/ps', '-ww', '-axo', 'command='], capture_output=True, text=True, check=True)
commands = processes.stdout.splitlines()
# A bare Python command could refer to an activated environment. Its open files
# may already be closed, so its ownership still cannot be inferred safely.
if any(re.match(r'''^\s*["']?python(?:w|[0-9.]*)?(?:["']?\s|$)''', c, re.I) and root.lower() not in c.lower() for c in commands):
    raise RuntimeError('Unattributed Python command')
files = subprocess.run(['/usr/sbin/lsof', '-nP', '-F', 'n', '+D', root], capture_output=True, text=True)
# lsof returns 1 without diagnostics when there are no matching open files.
if files.returncode not in (0, 1) or files.stderr.strip():
    raise RuntimeError('Environment file usage unavailable')
used = [c for c in commands if root.lower() in c.lower()]
used += [line[1:] for line in files.stdout.splitlines() if line.startswith('n')]
print(json.dumps(used))
`;
module.exports = {ENVIRONMENT_USAGE};
