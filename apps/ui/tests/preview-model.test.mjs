import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createPreviewProfiles, filterProfiles, runningProfiles, setPreviewStatus, closeAllPreviewProfiles } from '../src/preview-model.ts';

test('search trims input, ignores case, and leaves collection and running totals intact', () => {
  const profiles = createPreviewProfiles();
  assert.equal(profiles.length, 4);
  assert.equal(runningProfiles(profiles).length, 2);
  assert.deepEqual(filterProfiles(profiles, '  DEVELOPMENT ').map((p) => p.id), ['preview-development']);
  assert.equal(filterProfiles(profiles, 'nothing here').length, 0);
  assert.equal(filterProfiles(profiles, '   ').length, 4);
  assert.equal(filterProfiles([], '').length, 0);
  assert.equal(runningProfiles(profiles).length, 2);
});

test('launch, close, and close all update immutable preview state without stale tab counts', () => {
  const initial = createPreviewProfiles();
  const launched = setPreviewStatus(initial, 'preview-personal', 'running');
  assert.equal(runningProfiles(launched).length, 3);
  assert.equal(initial[0].status, 'closed');
  const closed = setPreviewStatus(launched, 'preview-development', 'closed');
  assert.equal(runningProfiles(closed).length, 2);
  assert.equal(closed[1].tabCount, undefined);
  const relaunched = setPreviewStatus(closed, 'preview-development', 'running');
  assert.equal(relaunched[1].tabCount, undefined);
  const allClosed = closeAllPreviewProfiles(relaunched);
  assert.equal(runningProfiles(allClosed).length, 0);
  assert.equal(allClosed.length, 4);
  assert.equal(initial[1].tabCount, 6);
  assert.deepEqual(closeAllPreviewProfiles([]), []);
  assert.deepEqual(createPreviewProfiles(), initial);
});
