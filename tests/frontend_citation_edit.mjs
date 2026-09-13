import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {getSchema} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {EditorState,TextSelection} from '@tiptap/pm/state';
import {citationBoundaryPlugin} from '../frontend/rich-document.js';

test('typing at a citation boundary excludes its mark without changing ordinary links or internal edits',()=>{
 const schema=getSchema([StarterKit]);
 const initial=href=>{
  const doc=schema.node('doc',null,[schema.node('paragraph',null,[schema.text('12',[schema.marks.link.create({href})])])]);
  return EditorState.create({doc,selection:TextSelection.create(doc,1),plugins:[citationBoundaryPlugin()]});
 };
 let citation=initial('#source-src_test');
 citation=citation.applyTransaction(citation.tr.setSelection(TextSelection.create(citation.doc,3))).state;
 citation=citation.applyTransaction(citation.tr.insertText(' 新增中文 12345')).state;
 const added=citation.doc.firstChild.lastChild;
 assert.equal(added.text,' 新增中文 12345');assert.equal(added.marks.length,0);
 let ordinary=initial('https://example.com');
 ordinary=ordinary.applyTransaction(ordinary.tr.setSelection(TextSelection.create(ordinary.doc,3))).state;
 ordinary=ordinary.applyTransaction(ordinary.tr.insertText(' suffix')).state;
 assert.equal(ordinary.doc.firstChild.lastChild.marks[0].attrs.href,'https://example.com');
 let inside=initial('#source-src_test');
 inside=inside.applyTransaction(inside.tr.setSelection(TextSelection.create(inside.doc,2))).state;
 inside=inside.applyTransaction(inside.tr.insertText('X')).state;
 assert.equal(inside.doc.firstChild.firstChild.text,'1X2');
 assert.equal(inside.doc.firstChild.firstChild.marks[0].attrs.href,'#source-src_test');
});

test('editor Markdown conversion preserves edited citation text and keeps bare references canonical',()=>{
 const source=fs.readFileSync(new URL('../frontend/app.js',import.meta.url),'utf8');
 const mapping=source.split('// BEGIN_FIGURE_EDITOR_MAPPING')[1].split('// END_FIGURE_EDITOR_MAPPING')[0];
 const context=vm.createContext({window:{location:{origin:'http://localhost'}},URL});
 vm.runInContext(mapping.slice(mapping.indexOf('\n'))+'\nthis.convert=fromEditor;',context);
 assert.equal(context.convert('[1](#source-src_test)'), '[@src_test]');
 assert.equal(context.convert('[1 新增中文 12345](#source-src_test)'), '1 新增中文 12345[@src_test]');
});
