const test = require('node:test');
const assert = require('node:assert/strict');
const {validateMarker} = require('../service.cjs');

test('startup accepts only the exact owned child and loopback launch marker', () => {
  const marker = {pid: 123, launch_id: 'own-launch', workspace_id: 'workspace', url: 'http://127.0.0.1:49152'};
  assert.equal(validateMarker(marker, {pid: 123}, 'own-launch'), marker.url);
  for (const changed of [{pid: 456}, {launch_id: 'stale-launch'}, {workspace_id: ''},
    ...['http://localhost:49152', 'https://127.0.0.1:49152', 'http://127.0.0.1:49152/other', 'http://user@127.0.0.1:49152'].map(url => ({url}))]) {
    assert.throws(() => validateMarker({...marker, ...changed}, {pid: 123}, 'own-launch'));
  }
});
