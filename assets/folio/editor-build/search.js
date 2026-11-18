import {Extension} from '@tiptap/core';
import {Plugin, PluginKey} from '@tiptap/pm/state';
import {Decoration, DecorationSet} from '@tiptap/pm/view';
import {findTextMatches} from '../text-search.mjs';

const key = new PluginKey('folioSearch');
export const folioSearchState = editor => key.getState(editor.state);
export const FolioSearch = Extension.create({
  name: 'folioSearch',
  addCommands() { return {setFolioSearch: (query = '', options = {}, active = -1) => ({tr}) => {
    tr.setMeta(key, {query, matchCase: !!options.matchCase, wholeWord: !!options.wholeWord, active});
    tr.setMeta('addToHistory', false);
    return true;
  }}; },
  addProseMirrorPlugins() { return [new Plugin({
    key,
    state: {
      init: () => ({query: '', matchCase: false, wholeWord: false, active: -1, matches: [], decorations: DecorationSet.empty}),
      apply(tr, previous) {
        const update = tr.getMeta(key);
        if (!update && !tr.docChanged) return previous;
        const next = {...previous, ...update};
        if (!next.query) return {...next, matches: [], decorations: DecorationSet.empty};
        const unchanged = !tr.docChanged && ['query', 'matchCase', 'wholeWord'].every(field => next[field] === previous[field]);
        if (unchanged && next.active === previous.active) return previous;
        const matches = unchanged ? previous.matches : findTextMatches(tr.doc, next.query, next);
        if (tr.docChanged) next.active = -1;
        const visible = matches.slice(0, 2000).map((match, index) => ({...match, index}));
        if (next.active >= 2000 && matches[next.active]) visible.push({...matches[next.active], index: next.active});
        return {...next, matches, decorations: DecorationSet.create(tr.doc, visible.map(match =>
          Decoration.inline(match.from, match.to, {class: match.index === next.active ? 'folio-search-hit folio-search-current' : 'folio-search-hit'})))};
      },
    },
    props: {decorations: state => key.getState(state).decorations},
  })]; },
});
