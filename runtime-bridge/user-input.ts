// The bridge retains native request identities; the UI answers question IDs,
// never permission option IDs. Validation is repeated here at the protocol edge.
export function questionAnswers(questions:any[],answers:any):Record<string,{answers:string[]}>{
 if(!answers||typeof answers!=='object'||Array.isArray(answers)||Object.keys(answers).length!==questions.length)throw Error('请回答每一个问题');
 const result=Object.create(null);
 for(const q of questions){
  const values=answers[q.id]?.answers;
  if(!Array.isArray(values)||!values.length||values.length>100||values.some(v=>typeof v!=='string'||!v.trim()||v.length>10000))throw Error('回答格式无效');
  const chosen=[...new Set(values.map((v:string)=>v.trim()))];
  if(!q.multiSelect&&chosen.length!==1)throw Error('该问题只能选择一个答案');
  if(q.allowCustom===false&&chosen.some(v=>!q.options.some(o=>o.label===v)))throw Error('请选择问题提供的选项');
  result[q.id]={answers:chosen};
 }
 return result;
}

export function claudeQuestions(input:any):any[]{
 const raw=input?.questions;
 if(!Array.isArray(raw)||!raw.length||raw.length>20)throw Error('AskUserQuestion 没有有效的问题');
 const texts=new Set();
 return raw.map((q:any,index:number)=>{
  if(typeof q?.question!=='string'||!q.question.trim()||texts.has(q.question))throw Error('AskUserQuestion 问题正文缺失或重复');
  texts.add(q.question);
  const options=q.options||[];
  if(!Array.isArray(options)||options.length>100||options.some(o=>typeof o?.label!=='string'||!o.label.trim()))throw Error('AskUserQuestion 选项格式无效');
  return {id:'question_'+index,question:q.question,header:typeof q.header==='string'?q.header:'',
   options:options.map(o=>({label:o.label,description:typeof o.description==='string'?o.description:''})),
   multiSelect:q.multiSelect===true,allowCustom:!options.length||q.allowCustom!==false};
 });
}

export function claudeQuestionInput(input:any,questions:any[],answers:any){
 const selected=questionAnswers(questions,answers);
 // Claude's native AskUserQuestion contract keys answers by original question
 // text and comma-joins multi-select labels, while preserving all other input.
 return {...input,answers:Object.fromEntries(questions.map(q=>[q.question,selected[q.id].answers.join(', ')]))};
}

export function piQuestion(request:any){
 const confirm=request.method==='confirm',select=request.method==='select';
 const labels=confirm?['确认','取消']:select?request.options:[];
 if(!Array.isArray(labels)||labels.length>100||labels.some(v=>typeof v!=='string'||!v.trim())||(select&&!labels.length))throw Error('Pi 问题选项格式无效');
 return {id:'answer',header:typeof request.title==='string'?request.title:'Pi',
  question:[request.title,request.message].filter(v=>typeof v==='string'&&v.trim()).join('\n')||'请输入回答',
  options:labels.map(label=>({label,description:''})),multiSelect:false,allowCustom:!confirm&&!select};
}
