// The two helpers every view needs. They were copied into five modules and
// injected into two more, so a page could escape markup one way and a panel
// another; there is one of each now.
export const $ = id => document.getElementById(id);

export const esc = value => String(value ?? '').replace(/[&<>"']/g,
 c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
