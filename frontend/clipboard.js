// Some desktop webviews deny the async Clipboard API even after a user click.
export async function copyText(text, doc=document, nav=navigator){
 try{await nav.clipboard.writeText(String(text));return}catch{}
 const previous=doc.activeElement;
 const field=doc.createElement('textarea');field.value=String(text);
 field.setAttribute('readonly','');field.style.cssText='position:fixed;left:-9999px;top:0';
 doc.body.append(field);
 try{field.select();if(!doc.execCommand('copy'))throw Error('无法访问剪贴板，请选中文字后复制。')}
 finally{field.remove();previous?.focus?.({preventScroll:true})}
}
