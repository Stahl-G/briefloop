// Keep the visible result while a refresh is in flight. Replacing identical
// markup resets expanded findings and can collapse a scrolled review pane.
export function beginPanel(box,version,placeholder=''){
 if(box.dataset.reportVersion===version)return;
 box.dataset.reportVersion=version;
 box.innerHTML=placeholder;
 box._reportHTML=placeholder;
}
export function updatePanel(box,html){
 if(box._reportHTML===html)return false;
 box.innerHTML=html;
 box._reportHTML=html;
 return true;
}
