// Retired brand color values are assembled here so the guard source itself
// does not reintroduce literal colors into repository assets.
const retired = new RegExp('(?:#|(?<![0-9a-f]))(?:'+['00'+'6838','14'+'5238','e6'+'f2ec'].join('|')+')(?![0-9a-f])', 'i');
export const containsRetiredBrandColor = source => retired.test(String(source));
