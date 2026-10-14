export function createSettingsNavigation({document: doc = globalThis.document, showPage, onReturn = () => {}}) {
  let origin = null;
  let scrollTop = 0;

  function open() {
    const current = doc.querySelector('main > section[id]:not([hidden])');
    if (doc.getElementById('settings-dialog')?.hidden !== false) {
      origin = current?.id || 'chat';
      scrollTop = doc.querySelector('main')?.scrollTop || 0;
    }
    showPage('settings-dialog');
  }

  function close() {
    const destination = origin || 'chat';
    showPage(destination);
    onReturn(destination);
    const scroller = doc.querySelector('main');
    if (scroller) scroller.scrollTop = scrollTop;
    origin = null;
  }

  return {open, close};
}
