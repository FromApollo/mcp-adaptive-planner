"""Query-focused extractive summaries. No model calls or invented source text.

TF-IDF estimates relevance and document centrality; MMR penalizes redundancy.
Character n-grams soften word-form differences in Russian without a model download.
Only complete sentences are eligible, including when enforcing output budgets.
"""
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
import math
import re

from markdown_it import MarkdownIt


_STOPWORDS = set("""a an and are as at be been but by can could do for from has have how if in into is
it its may more most not of on or our should so than that the their them then there
these they this to use was we were what when which who will with would you your
а без более был была были быть в вам вас весь во вот все всего всех вы где да для
до его ее если есть еще же за здесь и из или им их к как ко когда кто ли либо
меня может можно мы на над надо наш не него нее нет ни но о об он она они оно от
по под при про с сам со так также такой там те тем то того тоже только том ты у
уже чем что чтобы это этой этот эту я
""".split())
_WORDS = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
_NOISE = re.compile(
    r"cookie|куки|privacy policy|политик\w* конфиденциальност|all rights reserved|"
    r"все права защищены|subscribe|подписывайтесь|подпишитесь|sign (?:in|up)|"
    r"войти в аккаунт|accept all|принять все|advertisement|реклам\w* объявлен|"
    r"skip to (?:main|content)|перейти к содержимому|"
    r"в (?:этой|данной) статье мы|this (?:article|guide) (?:covers|will)|"
    r"независимо от того, (?:используете|хотите) ли вы", re.I,
)
_ABBREVIATION = re.compile(
    r"(?:\b(?:mr|mrs|ms|dr|prof|vs|etc|e\.g|i\.e|рис|табл|стр|им|ул|см|г|гг)"
    r"|\bт\.\s?[едп]|\bи\s+др)\.$", re.I,
)
_END = re.compile(r'[.!?]+[»”’"\)\]]*(?=\s|$)')
_AUX_HEADING = re.compile(
    r"^(?:related (?:articles|posts|content|videos|products)|you (?:may|might) also like|"
    r"comments|feedback|share this|связанные (?:статьи|материалы|видеоролики)|"
    r"читайте также|вам (?:также )?может (?:быть интересно|понравиться)|"
    r"дополнительная продукция|инструмент поиска продукции|спасибо за ваш отзыв|"
    r"обратная связь|комментарии|поделиться)", re.I,
)
_NEEDS_CONTEXT = re.compile(
    r"^(?:затем|после этого|для этого|это|эти|он|она|они|такой|такие|"
    r"this|these|it|they|then|next)\b|"
    r"\b(?:удали\w*|перемести\w*|отправ\w*|созда\w*|delete|move|change)\s+"
    r"(?:е[её]|его|их|это|it|them)\b|^выберите (?:диск|файл|его|ее|их)\b", re.I,
)
_MAX_INPUT_CHARS = 500_000
_MAX_CANDIDATES = 600


class _ArticleHTML(HTMLParser):
    """Remove non-content HTML without fetching anything."""

    _skip = {"script", "style", "nav", "header", "footer", "aside", "form", "svg", "noscript"}
    _blocks = {"p", "div", "article", "section", "li", "h1", "h2", "h3", "h4", "br", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._skip:
            self.hidden.append(tag)
        if not self.hidden and tag in self._blocks:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in self._blocks:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


@dataclass(frozen=True)
class Sentence:
    text: str
    heading: str
    prior: int | None = None
    count: int = 1


def _terms(text: str) -> Counter:
    words = [w for w in _WORDS.findall(text.casefold().replace("ё", "е"))
             if len(w) > 1 and w not in _STOPWORDS]
    counts = Counter("w:" + w for w in words)
    for word in words:
        if len(word) >= 4:
            # Lower weight than exact words; bounded per-word contribution.
            grams = {word[i:i + 4] for i in range(len(word) - 3)}
            for gram in grams:
                counts["c:" + gram] += 1 / math.sqrt(len(grams))
    return counts


def _sentences(text: str):
    start = 0
    for end in _END.finditer(text):
        candidate = text[start:end.end()].strip()
        if end.end() < len(text) and (
            _ABBREVIATION.search(candidate) or re.search(r"\b[A-ZА-ЯЁ]\.$", candidate)
        ):
            continue
        start = end.end()
        words = _WORDS.findall(candidate)
        # Search chunks can start/end mid-sentence. Never turn that tail into a fact.
        if (len(words) < 4 or not any(c.isupper() or c.isdigit() for c in words[0])
                or "..." in candidate or "…" in candidate or _NOISE.search(candidate)
                or candidate.count(">") >= 2 or re.search(r"\*{2,}|_{2,}", candidate)
                or re.search(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", candidate)):
            continue
        yield candidate
    # Deliberately do not emit an unterminated tail.


def _candidates(content: str) -> list[Sentence]:
    content = content[:_MAX_INPUT_CHARS]
    # Tavily separates disjoint snippets this way; do not join their cut edges.
    content = re.sub(r"\[\.\.\.\]|<chunk\s+\d+>", "\n\n", content, flags=re.I)
    if re.search(r"</?(?:p|div|article|nav|script|style|footer|header)\b", content, re.I):
        parser = _ArticleHTML()
        parser.feed(content)
        content = "".join(parser.parts)
    tokens = MarkdownIt("commonmark").enable("table").parse(content)
    first_title = next((i for i, t in enumerate(tokens) if t.type == "heading_open" and t.tag == "h1"), 0)
    tokens = tokens[first_title:]
    result, seen = [], set()
    heading = ""
    skip_level = None
    for index, token in enumerate(tokens):
        if token.type != "inline":
            continue  # Includes fenced code, images and HTML blocks.
        children = token.children or []
        text = "".join(t.content if t.type in ("text", "code_inline") else
                       " " if t.type in ("softbreak", "hardbreak") else ""
                       for t in children)
        text = re.sub(r"\s+", " ", text).strip()
        if index and tokens[index - 1].type == "heading_open":
            level = int(tokens[index - 1].tag[1])
            if skip_level is not None and level > skip_level:
                continue
            skip_level = level if _AUX_HEADING.search(text) else None
            heading = text
            continue
        if skip_level is not None:
            continue
        # Link-dominated menus are not prose, even if their labels contain periods.
        linked, link_chars = False, 0
        for child in children:
            if child.type == "link_open":
                linked = True
            elif child.type == "link_close":
                linked = False
            elif linked:
                link_chars += len(child.content)
        if (not text or link_chars / len(text) > 0.6
                or re.search(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", text)):
            continue
        paragraph = list(_sentences(text))
        # Short prose paragraphs often state a problem followed by its remedy.
        # Keep that pair together rather than extracting only the generic problem.
        paired = len(paragraph) == 2 and len(" ".join(paragraph)) <= 600
        lead = paragraph[0] if paragraph else ""
        if paired:
            paragraph = [" ".join(paragraph)]
        previous = None
        for sentence in paragraph:
            key = " ".join(_WORDS.findall(sentence.casefold()))
            if key not in seen:
                seen.add(key)
                needs_context = bool(_NEEDS_CONTEXT.search(lead if paired else sentence))
                if needs_context and previous is None:
                    continue
                prior = previous if needs_context or (previous is not None and sentence.startswith("Используйте ")) else None
                result.append(Sentence(sentence, heading, prior, 2 if paired else 1))
                previous = len(result) - 1
    return result


def _normalize(vector: dict) -> dict:
    norm = math.sqrt(sum(v * v for v in vector.values()))
    return {k: v / norm for k, v in vector.items()} if norm else {}


def _dot(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0) for k, v in a.items())


def summarize(content: str, query: str = "", *, title: str = "",
              max_chars: int = 1400, max_sentences: int = 5) -> str:
    """Select relevant, non-redundant sentences, retaining their source order.

    An empty string means there was no usable evidence within the budget.
    Headings help relevance but are never presented as standalone findings.
    """
    sentences = _candidates(content)
    if not sentences:
        return ""
    query_terms = _terms(query)
    if len(sentences) > _MAX_CANDIDATES:
        # Inspect the whole bounded page first, rather than taking its first N sentences.
        query_keys = set(query_terms)
        if query_terms:
            ranked = sorted(range(len(sentences)), key=lambda i: (
                len(query_keys.intersection(_terms(sentences[i].text + " " + sentences[i].heading))), -i
            ), reverse=True)
        else:
            ranked = [round(i * (len(sentences) - 1) / (_MAX_CANDIDATES - 1))
                      for i in range(_MAX_CANDIDATES)]
        keep = set()
        for index in ranked[:_MAX_CANDIDATES]:
            bundle = set()
            while index is not None and index not in keep:
                bundle.add(index)
                index = sentences[index].prior
            if len(keep | bundle) <= _MAX_CANDIDATES:
                keep.update(bundle)
        indices = {old: new for new, old in enumerate(sorted(keep))}
        sentences = [Sentence(s.text, s.heading, indices.get(s.prior), s.count)
                     for i, s in enumerate(sentences) if i in keep]
    if not sentences:
        return ""
    bags = [_terms(s.text) for s in sentences]
    frequency = Counter(term for bag in bags for term in bag)
    idf = {term: 1 + math.log((len(bags) + 1) / (count + 1))
           for term, count in frequency.items()}

    def vector(bag):
        # Keep unseen query terms in its norm: one shared suffix must not become
        # a perfect match just because the rest of the query is absent from a page.
        unseen_idf = 1 + math.log(len(bags) + 1)
        return _normalize({k: (1 + math.log(v) if v >= 1 else v) * idf.get(k, unseen_idf)
                           for k, v in bag.items()})

    vectors = [vector(bag) for bag in bags]
    query_vector = vector(query_terms)
    title_vector = vector(_terms(title))
    total = Counter()
    for v in vectors:
        total.update(v)
    centroid = _normalize(total)
    relevance = []
    query_scores = []
    for sentence, v in zip(sentences, vectors):
        match = _dot(v, query_vector)
        context = _dot(vector(_terms(sentence.heading)), query_vector)
        query_scores.append(match + 0.15 * context)
        centrality = _dot(v, centroid)
        title_match = _dot(v, title_vector)
        relevance.append((0.7 * match + 0.1 * context + 0.15 * centrality + 0.05 * title_match)
                         if query_terms else 0.85 * centrality + 0.15 * title_match)
    # Do not pad a focused summary with unrelated sentences. If nothing matches,
    # return no evidence instead of silently switching to an unrelated page summary.
    best_match = max(query_scores)
    if query_terms and best_match < 0.025:
        return ""
    eligible = {i for i in range(len(sentences))
                if not query_terms or query_scores[i] >= max(0.025, best_match * 0.2)}
    best_relevance = max(relevance) or 1
    relevance = [score / best_relevance for score in relevance]
    selected, used = [], 0
    while eligible and sum(sentences[i].count for i in selected) < max_sentences:
        def mmr(i):
            redundancy = max((_dot(vectors[i], vectors[j]) for j in selected), default=0)
            return 0.7 * relevance[i] - 0.3 * redundancy

        chosen = max(eligible, key=lambda i: (mmr(i), -i))
        eligible.remove(chosen)
        bundle, index = [], chosen
        while index is not None and index not in selected:
            bundle.append(index)
            index = sentences[index].prior
        similarity = max((_dot(vectors[chosen], vectors[j]) for j in selected), default=0)
        cost = sum(len(sentences[i].text) + 1 for i in bundle) - (0 if selected else 1)
        if (similarity >= 0.8 or used + cost > max_chars or mmr(chosen) <= 0
                or sum(sentences[i].count for i in selected + bundle) > max_sentences):
            continue
        selected.extend(bundle)
        eligible.difference_update(bundle)
        used += cost
    return " ".join(sentences[i].text for i in sorted(selected))
