// Read-only comparison. No marks are ever written into a saved document.
const key=node=>JSON.stringify(node,(k,v)=>k==='blockId'?undefined:v);
export function diffRows(before=[],after=[]){
 const a=before.map(key),b=after.map(key),width=b.length+1;
 const dp=new Uint32Array((a.length+1)*width);
 for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)dp[i*width+j]=a[i]===b[j]?1+dp[(i+1)*width+j+1]:Math.max(dp[(i+1)*width+j],dp[i*width+j+1]);
 const rows=[];let i=0,j=0,removed=[],added=[];
 function flush(){for(let k=0;k<Math.max(removed.length,added.length);k++)rows.push({before:removed[k],after:added[k],changed:true});removed=[];added=[]}
 while(i<a.length||j<b.length){
  if(i<a.length&&j<b.length&&a[i]===b[j]){flush();rows.push({before:before[i++],after:after[j++],changed:false})}
  else if(i<a.length&&(j===b.length||dp[(i+1)*width+j]>=dp[i*width+j+1]))removed.push(before[i++]);
  else added.push(after[j++]);
 }
 flush();return rows;
}
export function changedRange(a,b){
 let start=0;while(start<a.length&&start<b.length&&a[start]===b[start])start++;
 let endA=a.length,endB=b.length;while(endA>start&&endB>start&&a[endA-1]===b[endB-1]){endA--;endB--}
 return {start,endA,endB};
}
function highlight(root,start,end,kind){
 const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);const nodes=[];let offset=0,node;
 while((node=walker.nextNode())){nodes.push({node,offset});offset+=node.length}
 for(const {node,offset} of nodes){const lo=Math.max(0,start-offset),hi=Math.min(node.length,end-offset);if(hi<=lo)continue;
  const range=document.createRange();range.setStart(node,lo);range.setEnd(node,hi);const mark=document.createElement(kind==='removed'?'del':'ins');mark.className='diff-'+kind;range.surroundContents(mark);
 }
}
export function renderVersionDiff(container,before,after,renderNode){
 container.replaceChildren();const rows=diffRows(before,after);let changes=0;
 for(const row of rows){
  const line=document.createElement('div');line.className='version-diff-row';
  const left=document.createElement('div'),right=document.createElement('div');left.className=right.className='version-diff-cell';
  if(row.before)left.append(renderNode(row.before,'before'));if(row.after)right.append(renderNode(row.after,'after'));
  if(row.changed){changes++;line.dataset.change=String(changes);
   const a=left.textContent,b=right.textContent,r=changedRange(a,b);
   if(a!==b){highlight(left,r.start,r.endA,'removed');highlight(right,r.start,r.endB,'added')}
   if(!row.after||a===b)left.classList.add('diff-block-removed');if(!row.before||a===b)right.classList.add('diff-block-added');
   left.setAttribute('aria-label',row.before?'修改前':'无原内容');right.setAttribute('aria-label',row.after?'修改后':'已删除');
  }
  line.append(left,right);container.append(line);
 }
 return changes;
}
