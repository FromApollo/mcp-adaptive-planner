from docx import Document
from docx.oxml.ns import qn
from mcp_servers.reports.server import create_docx


def test_docx_formats_search_markdown_as_native_word_content(tmp_path):
    target = tmp_path / 'report.docx'
    create_docx('Результаты поиска', '''# Рекомендации

**Важный результат** и *пояснение*.

- Первый пункт
- Второй пункт с `кодом`

3. Третий пункт
4. Четвёртый пункт

[Источник](https://example.com/docs)

| Название | Значение |
| --- | --- |
| Память | **16 ГБ** |
''', str(target))
    doc = Document(target)
    text = '\n'.join(p.text for p in doc.paragraphs)
    assert '# Рекомендации' not in text
    assert '**' not in text and '[Источник]' not in text
    heading = next(p for p in doc.paragraphs if p.text == 'Рекомендации')
    assert heading.style.name == 'Heading 1'
    paragraph = next(p for p in doc.paragraphs if 'Важный результат' in p.text)
    assert any(r.text == 'Важный результат' and r.bold for r in paragraph.runs)
    assert any(r.text == 'пояснение' and r.italic for r in paragraph.runs)
    bullets = [p for p in doc.paragraphs if p._p.xpath('./w:pPr/w:numPr')]
    assert len(bullets) == 4
    assert doc.part.numbering_part.element.xpath('.//w:start[@w:val="3"]')
    assert len(doc.tables) == 1
    assert doc.tables[0].cell(1, 1).text == '16 ГБ'
    assert any(run.text == '16 ГБ' and run.bold for run in doc.tables[0].cell(1, 1).paragraphs[0].runs)
    links = doc.element.xpath('.//w:hyperlink')
    assert len(links) == 1
    assert doc.part.rels[links[0].get(qn('r:id'))].target_ref == 'https://example.com/docs'


def test_docx_preserves_plain_text_code_and_line_breaks(tmp_path):
    target = tmp_path / 'plain.docx'
    create_docx('Пример', 'Первая строка\nВторая строка\n\n```text\n# это код\n**буквально**\n```', str(target))
    doc = Document(target)
    assert doc.paragraphs[1].text == 'Первая строка\nВторая строка'
    assert doc.paragraphs[2].text == '# это код\n**буквально**'
    assert doc.paragraphs[2].runs[0].font.name == 'Consolas'


def test_docx_never_creates_active_unsafe_links_or_loads_remote_images(tmp_path):
    target = tmp_path / 'safe.docx'
    content = '<script>test</script>\n\n[Bad](javascript:alert(1))\n\n![Remote](https://example.com/private.png)'
    create_docx('Безопасность', content, str(target))
    doc = Document(target)
    assert not doc.inline_shapes
    assert not any('javascript:' in str(rel.target_ref) for rel in doc.part.rels.values())
    assert '<script>test</script>' in '\n'.join(p.text for p in doc.paragraphs)


def test_nested_lists_and_empty_content_do_not_fail(tmp_path):
    target = tmp_path / 'nested.docx'
    create_docx('Список', '- Родитель\n  - Дочерний\n\n  Продолжение\n\n> Цитата', str(target))
    doc = Document(target)
    assert any(p.text == 'Цитата' and p.style.name == 'Quote' for p in doc.paragraphs)
    assert len([p for p in doc.paragraphs if p._p.xpath('./w:pPr/w:numPr')]) == 2
    create_docx('Пустой документ', '', str(target))
    assert len(Document(target).paragraphs) == 1
