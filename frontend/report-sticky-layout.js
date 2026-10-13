// The report header wraps as the title, actions and viewport change.
// Keep the rail below its actual height and let the pane use the remaining viewport.
export function createReportStickyLayout({report,header,scroller,panel,grid,column,documentArea,jobs,window:win=globalThis.window,
 ResizeObserver:Observer=globalThis.ResizeObserver,MutationObserver:VisibilityObserver=globalThis.MutationObserver}){
 if(!report||!header||!Observer)return;
 const originalParent=jobs?.parentNode;
 // A sticky grid item is bounded by its row. Include trailing jobs in the
 // document column so they cannot push the rail above the report header.
 // Keep the existing full-width jobs outside hidden/outline and narrow layouts.
 const placeJobs=()=>{
  if(!jobs||!originalParent||!column||!grid||!documentArea||!panel)return;
  const sideBySide=!grid.hidden&&!documentArea.hidden&&win.getComputedStyle(panel).position==='sticky';
  const target=sideBySide?column:originalParent;
  if(jobs.parentNode!==target)target.append(jobs);
 };
 const update=()=>{
  placeJobs();
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
 const visibility=VisibilityObserver?new VisibilityObserver(update):null;
 for(const element of [grid,documentArea])if(element)visibility?.observe(element,{attributes:true,attributeFilter:['hidden']});
 update();
 return {disconnect:()=>{observer.disconnect();visibility?.disconnect()}};
}
