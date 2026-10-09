'use strict';
// Read-only check of the active locked requirements. No resolver or network.
const CHECK_LOCK = String.raw`import importlib.metadata,json,pathlib,sys
from pip._vendor.packaging.requirements import Requirement
matches = True
checked = 0
try:
    for raw in pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith(('#', '--hash=')):
            continue
        declaration = line.split(' --hash=', 1)[0].rstrip(' \\')
        req = Requirement(declaration)
        if req.url or req.extras or not req.specifier or any(s.operator != '==' or '*' in s.version for s in req.specifier):
            raise ValueError('not a pinned lock entry')
        if req.marker and not req.marker.evaluate():
            continue
        checked += 1
        if not req.specifier.contains(importlib.metadata.version(req.name), prereleases=True):
            matches = False
except Exception:
    matches = False
print(json.dumps({'matches': matches and checked > 0}))
`;
module.exports = {CHECK_LOCK};
