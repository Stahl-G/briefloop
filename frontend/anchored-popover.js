// Position against the visible viewport, not the composer's available height.
// Keep a single scrollable panel when neither side can fit all of its content.
export function popoverBounds(anchor,size,viewport,{margin=8,gap=8}={}){
 const left=viewport.left+margin,top=viewport.top+margin;
 const right=viewport.left+viewport.width-margin,bottom=viewport.top+viewport.height-margin;
 const width=Math.min(size.width,Math.max(0,right-left));
 const above=Math.max(0,Math.min(anchor.top-gap,bottom)-top);
 const below=Math.max(0,bottom-Math.max(anchor.bottom+gap,top));
 const up=size.height<=above||above>=below,maxHeight=up?above:below;
 // At extreme zoom neither side may fit even the panel padding: use the
 // visible viewport rather than a zero-height box anchored offscreen.
 if(maxHeight<Math.min(size.height,96))return {left:Math.max(left,Math.min(anchor.right-width,right-width)),top,width,maxHeight:Math.max(0,bottom-top)};
 const height=Math.min(size.height,maxHeight);
 return {left:Math.max(left,Math.min(anchor.right-width,right-width)),top:Math.max(top,Math.min(up?anchor.top-gap-height:anchor.bottom+gap,bottom-height)),width,maxHeight};
}

export function anchoredPopover({trigger,panel,document:doc=globalThis.document,window:win=globalThis.window,ResizeObserver:Observer=globalThis.ResizeObserver}){
 if(!trigger||!panel)return;
 // Body placement avoids ancestor overflow and transformed composer coordinates.
 doc.body.append(panel);panel.classList.remove('popover');panel.setAttribute('role','region');panel.setAttribute('aria-label','研究选项');
 function close({restoreFocus=false}={}){panel.hidden=true;trigger.setAttribute('aria-expanded','false');if(restoreFocus)trigger.focus()}
 function reposition(){
  if(panel.hidden)return;
  const anchor=trigger.getBoundingClientRect(),view=win.visualViewport;
  const viewport={left:view?.offsetLeft||0,top:view?.offsetTop||0,width:view?.width||win.innerWidth,height:view?.height||win.innerHeight};
  if(!trigger.getClientRects().length||anchor.bottom<viewport.top||anchor.top>viewport.top+viewport.height||anchor.right<viewport.left||anchor.left>viewport.left+viewport.width){close();return}
  panel.style.maxWidth=Math.max(0,viewport.width-16)+'px';
  const rect=panel.getBoundingClientRect();
  const bounds=popoverBounds(anchor,{width:rect.width,height:panel.scrollHeight+Math.max(0,rect.height-panel.clientHeight)},viewport);
  Object.assign(panel.style,{left:bounds.left+'px',top:bounds.top+'px',maxHeight:bounds.maxHeight+'px'});
 }
 function open(){
  doc.querySelectorAll('.popover').forEach(node=>node.hidden=true);
  panel.hidden=false;panel.scrollTop=0;trigger.setAttribute('aria-expanded','true');reposition();
  panel.querySelector('button:not(:disabled),select:not(:disabled),input:not(:disabled),summary')?.focus({preventScroll:true});
 }
 trigger.onclick=event=>{event.stopPropagation();if(panel.hidden)open();else close({restoreFocus:true})};
 doc.addEventListener('pointerdown',event=>{if(!panel.hidden&&!panel.contains(event.target)&&!trigger.contains(event.target))close()});
 doc.addEventListener('keydown',event=>{if(event.key==='Escape'&&!panel.hidden){event.preventDefault();close({restoreFocus:true})}});
 doc.addEventListener('focusin',event=>{if(!panel.hidden&&!panel.contains(event.target)&&!trigger.contains(event.target))close()});
 doc.addEventListener('scroll',event=>{if(!panel.contains(event.target))reposition()},true);
 panel.addEventListener('toggle',reposition,true);win.addEventListener('resize',reposition);
 win.visualViewport?.addEventListener('resize',reposition);win.visualViewport?.addEventListener('scroll',reposition);
 const observer=Observer?new Observer(reposition):null;observer?.observe(panel);observer?.observe(trigger);
 // Source attachments and textarea growth can move an unchanged-size trigger.
 for(const ancestor of [trigger.closest?.('form'),trigger.closest?.('.chat-page')])if(ancestor)observer?.observe(ancestor);
 return {open,close,reposition};
}
