import {esc} from './ui.js';

let parser;
export function renderMarkdown(value) {
  if (!parser && typeof globalThis.markdownit === 'function') {
    parser = globalThis.markdownit({html: false, breaks: true, linkify: false}).disable('image');
    parser.validateLink = url => {
      try { return ['http:', 'https:', 'mailto:'].includes(new URL(url, 'http://localhost/').protocol); }
      catch { return false; }
    };
    const defaultLink = parser.renderer.rules.link_open || ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options));
    parser.renderer.rules.link_open = (tokens, index, ...args) => {
      tokens[index].attrSet('target', '_blank');
      tokens[index].attrSet('rel', 'noopener noreferrer');
      return defaultLink(tokens, index, ...args);
    };
  }
  // Keep results readable even if the parser asset failed to load.
  return parser ? parser.render(value) : `<pre>${esc(value)}</pre>`;
}
