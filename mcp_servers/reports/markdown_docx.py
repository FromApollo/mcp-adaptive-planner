"""Convert Markdown to native Word content; never execute HTML or fetch images."""
from urllib.parse import urlsplit

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as REL
from docx.shared import Inches, Pt, RGBColor
from docx.text.run import Run
from markdown_it import MarkdownIt


def _safe_link(url):
    try:
        return urlsplit(url).scheme.lower() in ('', 'http', 'https', 'mailto')
    except ValueError:
        return False


def _parser():
    parser = MarkdownIt('commonmark', {'html': False}).enable('table').disable('image')
    parser.validateLink = _safe_link
    return parser


def _inline(paragraph, children):
    bold = italic = 0
    hyperlink = None
    for token in children or []:
        if token.type == 'strong_open':
            bold += 1
        elif token.type == 'strong_close':
            bold -= 1
        elif token.type == 'em_open':
            italic += 1
        elif token.type == 'em_close':
            italic -= 1
        elif token.type == 'link_open':
            url = token.attrGet('href') or ''
            if _safe_link(url):
                hyperlink = OxmlElement('w:hyperlink')
                hyperlink.set(qn('r:id'), paragraph.part.relate_to(url, REL.HYPERLINK, is_external=True))
                paragraph._p.append(hyperlink)
        elif token.type == 'link_close':
            hyperlink = None
        elif token.type in ('text', 'code_inline', 'softbreak', 'hardbreak'):
            if hyperlink is None:
                run = paragraph.add_run()
            else:
                element = OxmlElement('w:r')
                hyperlink.append(element)
                run = Run(element, paragraph)
                run.font.color.rgb = RGBColor.from_string('4F46E5')
                run.underline = True
            if token.type in ('softbreak', 'hardbreak'):
                run.add_break()
            else:
                run.text = token.content
                run.bold = True if bold else None
                run.italic = True if italic else None
                if token.type == 'code_inline':
                    run.font.name = 'Consolas'
                    run.font.size = Pt(10)


def _numbering(document, ordered, start):
    root = document.part.numbering_part.element
    abstract_id = max([int(e.get(qn('w:abstractNumId'))) for e in root.findall(qn('w:abstractNum'))] + [-1]) + 1
    abstract = OxmlElement('w:abstractNum')
    abstract.set(qn('w:abstractNumId'), str(abstract_id))
    level = OxmlElement('w:lvl'); level.set(qn('w:ilvl'), '0')
    for tag, value in [('start', str(start)), ('numFmt', 'decimal' if ordered else 'bullet'), ('lvlText', '%1.' if ordered else '•')]:
        child = OxmlElement('w:' + tag); child.set(qn('w:val'), value); level.append(child)
    abstract.append(level); root.append(abstract)
    number = root.add_num(abstract_id)
    return number.numId


def append_markdown(document, content):
    tokens = _parser().parse(content)
    paragraph = None
    lists = []
    quote_depth = 0
    table = row = cell = None
    column = 0
    for index, token in enumerate(tokens):
        kind = token.type
        if kind in ('bullet_list_open', 'ordered_list_open'):
            ordered = kind == 'ordered_list_open'
            lists.append({'number': _numbering(document, ordered, int(token.attrGet('start') or 1)), 'fresh': False})
        elif kind in ('bullet_list_close', 'ordered_list_close'):
            lists.pop()
        elif kind == 'list_item_open':
            lists[-1]['fresh'] = True
        elif kind == 'blockquote_open':
            quote_depth += 1
        elif kind == 'blockquote_close':
            quote_depth -= 1
        elif kind == 'table_open':
            first_row = tokens[index + 1:]
            count = 0
            for item in first_row:
                if item.type in ('th_open', 'td_open'):
                    count += 1
                if item.type == 'tr_close':
                    break
            table = document.add_table(rows=0, cols=max(1, count))
            table.style = 'Table Grid'
        elif kind == 'tr_open':
            row = table.add_row(); column = 0
        elif kind in ('th_open', 'td_open'):
            cell = row.cells[column]; column += 1
            paragraph = cell.paragraphs[0]
        elif kind in ('th_close', 'td_close'):
            if kind == 'th_close':
                for run in paragraph.runs:
                    run.bold = True
            cell = paragraph = None
        elif kind == 'table_close':
            table = row = cell = None
        elif kind in ('paragraph_open', 'heading_open'):
            if cell is not None:
                paragraph = cell.paragraphs[0]
            elif kind == 'heading_open':
                paragraph = document.add_heading(level=min(6, int(token.tag[1:])))
            else:
                paragraph = document.add_paragraph(style='Quote' if quote_depth else None)
            if lists:
                paragraph.paragraph_format.left_indent = Inches(.25 * len(lists))
                if lists[-1]['fresh']:
                    properties = paragraph._p.get_or_add_pPr()
                    number = OxmlElement('w:numPr')
                    level = OxmlElement('w:ilvl'); level.set(qn('w:val'), '0')
                    number_id = OxmlElement('w:numId'); number_id.set(qn('w:val'), str(lists[-1]['number']))
                    number.extend([level, number_id]); properties.append(number)
                    paragraph.paragraph_format.first_line_indent = Inches(-.15)
                    lists[-1]['fresh'] = False
        elif kind == 'inline':
            if paragraph is None:
                paragraph = document.add_paragraph()
            _inline(paragraph, token.children)
        elif kind in ('paragraph_close', 'heading_close'):
            paragraph = None
        elif kind in ('fence', 'code_block'):
            paragraph = document.add_paragraph()
            run = paragraph.add_run(token.content.rstrip('\n'))
            run.font.name = 'Consolas'; run.font.size = Pt(10)
            paragraph = None
        elif kind == 'hr':
            document.add_paragraph('────────────────────')
