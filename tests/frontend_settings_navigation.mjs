import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createSettingsNavigation} from '../frontend/settings-navigation.js';

test('closing settings restores the originating page and scroll, including repeated settings opens', () => {
  const panes = ['report', 'sources', 'chat', 'settings-dialog'].map(id => ({id, hidden: id !== 'report'}));
  const scroller = {scrollTop: 520};
  const visits = [];
  const showPage = id => {
    visits.push(id);
    panes.forEach(pane => {pane.hidden = pane.id !== id;});
    scroller.scrollTop = 0;
  };
  const doc = {querySelector: selector => selector === 'main' ? scroller : panes.find(pane => !pane.hidden),
    getElementById: id => panes.find(pane => pane.id === id)};
  const navigation = createSettingsNavigation({document: doc, showPage});
  navigation.open();
  scroller.scrollTop = 200;
  navigation.open();
  navigation.close();
  assert.equal(panes.find(pane => !pane.hidden).id, 'report');
  assert.equal(scroller.scrollTop, 520);
  assert.ok(!visits.includes('chat'));

  showPage('sources');
  scroller.scrollTop = 130;
  navigation.open();
  navigation.close();
  assert.equal(panes.find(pane => !pane.hidden).id, 'sources');
  assert.equal(scroller.scrollTop, 130);
});
