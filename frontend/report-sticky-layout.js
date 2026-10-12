// The report header wraps as the title, actions and viewport change.
// Keep the rail below its actual height and let the pane use the remaining viewport.
export function createReportStickyLayout({report,header,scroller,window:win=globalThis.window,
 ResizeObserver:Observer=globalThis.ResizeObserver}){
 if(!report||!header||!Observer)return;
 const update=()=>{
  const height=header.getBoundingClientRect().height;
  if(height>0)report.style.setProperty('--report-header-height',`${Math.ceil(height)}px`);
  if(scroller){
   const style=win.getComputedStyle(scroller);
   const available=scroller.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
   if(available>0)report.style.setProperty('--report-viewport-height',`${Math.floor(available)}px`);
  }
 };
 const observer=new Observer(update);
 observer.observe(header);
 if(scroller)observer.observe(scroller);
 update();
 return {disconnect:()=>observer.disconnect()};
}
