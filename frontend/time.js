// One set of time formats for the page.
//
// There were ten call sites with ten spellings, and five of them passed no
// locale at all — in a Chinese interface a bare toLocaleString() prints
// "9/21/2026, 5:40:05 PM" on an en-US browser. Every format names zh-CN, and
// an unparseable value renders as nothing rather than "Invalid Date".
const format = (value, options) => {
 const date = new Date(value);
 return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', {hour12: false, ...options});
};

/** 9/21 — a source's day, where the year is obvious from context. */
export const day = value => format(value, {month: 'numeric', day: 'numeric'});
/** 9/21 17:40 — report cards and lists. */
export const dayTime = value => format(value, {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
/** 2026/09/21 17:40 — a source's full record. */
export const dateTime = value => format(value, {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
/** 2026/09/21 17:40:05 — version history, where ordering matters to the second. */
export const dateTimeSeconds = value => format(value, {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'});
/** 17:40 — a message inside a conversation. */
export const clock = value => format(value, {hour: '2-digit', minute: '2-digit'});
/** 2026/9/21 17:40:05 — everything else that wants the whole moment. */
export const moment = value => format(value);
/** The same moment in a schedule's own zone. */
export const inZone = (value, timeZone) => format(value, timeZone ? {timeZone} : undefined);
