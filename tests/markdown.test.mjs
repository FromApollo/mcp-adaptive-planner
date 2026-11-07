import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {renderMarkdown} from '../frontend/markdown.js';

const sandbox = {module: {exports: {}}};
sandbox.exports = sandbox.module.exports;
vm.runInNewContext(readFileSync(new URL('../frontend/vendor/markdown-it.min.js', import.meta.url), 'utf8'), sandbox);
globalThis.markdownit = sandbox.module.exports;

test('log renders headings, emphasis, lists, tables and real links', () => {
  const html = renderMarkdown('# Заголовок\n\n**Важно** и *курсив*\n\n- Пункт\n\n[Источник](https://example.com)\n\n| A | B |\n| --- | --- |\n| 1 | 2 |');
  assert.ok(html.includes('<h1>Заголовок</h1>'));
  assert.ok(html.includes('<strong>Важно</strong>'));
  assert.ok(html.includes('<em>курсив</em>'));
  assert.ok(html.includes('<ul>'));
  assert.ok(html.includes('<table>'));
  assert.ok(html.includes('target="_blank"'));
  assert.ok(html.includes('rel="noopener noreferrer"'));
  assert.equal(html.includes('**Важно**'), false);
});

test('untrusted web content cannot execute HTML or load remote images', () => {
  const html = renderMarkdown('<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n![Image](https://example.com/tracker.png)\n\n[data](data:text/html,test)');
  assert.equal(html.includes('<script>'), false);
  assert.equal(html.includes('href="javascript:'), false);
  assert.equal(html.includes('href="data:'), false);
  assert.equal(html.includes('<img'), false);
  assert.ok(html.includes('&lt;script&gt;'));
});

test('code stays literal while plain text line breaks remain readable', () => {
  const html = renderMarkdown('Строка 1\nСтрока 2\n\n```text\n# code\n**literal**\n```');
  assert.ok(html.includes('Строка 1<br>'));
  assert.ok(html.includes('# code\n**literal**'));
  assert.ok(html.includes('<pre><code'));
});
