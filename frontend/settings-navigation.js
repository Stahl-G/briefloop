export function createSettingsNavigation({document: doc = globalThis.document, showPage}) {
  let origin = null;
  let scrollTop = 0;

  function open() {
    const current = doc.querySelector('main > section:not([hidden])');
    if (current?.id !== 'settings-dialog') {
      origin = current?.id || 'chat';
      scrollTop = doc.querySelector('main')?.scrollTop || 0;
    }
    showPage('settings-dialog');
  }

  function close() {
    showPage(origin || 'chat');
    const scroller = doc.querySelector('main');
    if (scroller) scroller.scrollTop = scrollTop;
    origin = null;
  }

  return {open, close};
}
