'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {configuration} = require('../scripts/build-mac-signed.cjs');
const pkg = require('../package.json');
test('release signing fails closed without identity or with ambiguous notarization auth', () => {
  assert.throws(()=>configuration({},pkg), /SIGNING_IDENTITY/);
  assert.throws(()=>configuration({BRIEFLOOP_APPLE_SIGNING_IDENTITY:'-'},pkg), /SIGNING_IDENTITY/);
  assert.throws(()=>configuration({BRIEFLOOP_APPLE_SIGNING_IDENTITY:'Example (TEAM)'},pkg), /KEYCHAIN_PROFILE/);
  assert.throws(()=>configuration({BRIEFLOOP_APPLE_SIGNING_IDENTITY:'Example (TEAM)',APPLE_KEYCHAIN_PROFILE:'example',APPLE_ID:'ambiguous'},pkg), /only the Keychain/);
});
test('signed release overrides unsigned defaults without changing backend packaging', () => {
  const config = configuration({BRIEFLOOP_APPLE_SIGNING_IDENTITY:'Example (TEAM)',APPLE_KEYCHAIN_PROFILE:'example'},pkg);
  assert.equal(config.forceCodeSigning,true);
  assert.equal(config.mac.notarize,true);
  assert.equal(config.mac.hardenedRuntime,true);
  assert.equal(config.dmg.sign,true);
  assert.deepEqual(config.extraResources,pkg.build.extraResources);
  assert.equal(pkg.build.mac.identity,null);
  assert.equal(config.publish,null);
});
