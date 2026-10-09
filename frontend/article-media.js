// The same source panel used for PDF/image originals also shows article images.
// Reading a preview is never labeled as model inspection or factual validation.
export function showArticleMedia(result,view,{document:doc=globalThis.document}={}){
 const article=result.attachment?.article;if(!article)return false;
 view.media.hidden=false;view.controls.hidden=true;
 const images=article.images||[],available=images.filter(row=>row.status==='available').length;
 view.note.textContent=`${available} / ${images.length} 张原图可查看。未执行 OCR；图片保存不代表模型已阅读。`;
 const description=doc.createElement('p');description.className='help';
 description.textContent=[article.publisher,article.published_at?'发布：'+article.published_at:null].filter(Boolean).join(' · ');
 view.images.append(description);
 for(const row of images){
  const box=doc.createElement('details'),summary=doc.createElement('summary'),note=doc.createElement('p');
  summary.textContent=`${row.role==='cover'?'封面':'原图 '+row.original_index} · ${row.status==='available'?'查看原图':row.status==='missing'?'缺少原图':'无法读取'}`;
  const duplicate=images.find(item=>item.number===row.same_bytes_as);
  note.className='help';note.textContent=row.note+(duplicate?`；与${duplicate.role==='cover'?'封面':'原图 '+duplicate.original_index}内容字节相同，需核对用途。`:'');
  box.append(summary,note);
  if(row.status==='available'){
   const img=doc.createElement('img');img.className='source-preview-image';img.alt=row.alt||summary.textContent;
   // Fetch only when requested; don't turn hundreds of images into an eager gallery.
   box.ontoggle=()=>{if(box.open&&!img.getAttribute('src'))img.src='/api/source-image?id='+encodeURIComponent(result.source.id)+'&image='+row.number};
   box.append(img);
  }
  view.images.append(box);
 }
 return true;
}
