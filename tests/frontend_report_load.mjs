import test from 'node:test';
import assert from 'node:assert/strict';
import {getSchema} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {EditorState,TextSelection} from '@tiptap/pm/state';
import {trailingParagraphPlugin} from '../frontend/rich-document.js';

test('opening or selecting a report ending in a list preserves its saved document; real edits remain possible',()=>{
 const schema=getSchema([StarterKit.configure({trailingNode:false})]);
 const doc=schema.node('doc',null,[schema.node('bulletList',null,[schema.node('listItem',null,[schema.node('paragraph',null,[schema.text('Saved decision')])])])]);
 let state=EditorState.create({doc,plugins:[trailingParagraphPlugin()]});
 const focused=state.applyTransaction(state.tr.setSelection(TextSelection.create(doc,3)));
 assert.equal(focused.transactions.some(tr=>tr.docChanged),false);
 assert.deepEqual(focused.state.doc.toJSON(),doc.toJSON());
 state=focused.state.applyTransaction(focused.state.tr.setMeta('reader-highlight',true)).state;
 assert.deepEqual(state.doc.toJSON(),doc.toJSON());
 state=state.applyTransaction(state.tr.insertText('New ')).state;
 assert.equal(state.doc.firstChild.textContent,'New Saved decision');
 assert.equal(state.doc.lastChild.type.name,'paragraph');
 // The user can type after the list; the next selection does not add more nodes.
 const end=state.doc.content.size-1;
 state=state.applyTransaction(state.tr.setSelection(TextSelection.create(state.doc,end))).state;
 state=state.applyTransaction(state.tr.insertText('After the list')).state;
 assert.equal(state.doc.lastChild.textContent,'After the list');
 assert.equal(state.doc.childCount,2);
});
