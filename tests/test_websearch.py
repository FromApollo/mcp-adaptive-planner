"""Offline regressions for evidence quality, not live search ranking."""
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_servers.websearch import server
from mcp_servers.websearch.summarize import summarize
from orchestrator.errors import tool_result_error


@pytest.fixture
def tavily(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(server.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(server, "_api_key", lambda: "test-key")
    return client


def test_relevant_advice_beats_intro_and_incomplete_tail():
    intro = "Наша компания давно выпускает компьютеры для дома и офиса. " * 40
    advice = "Освободите место на системном диске, удалив ненужные временные файлы."
    content = intro + "\n\n" + advice + " Дальнейшее увеличение скорости связано с освобождением памя"
    summary = summarize(content, "освобождение места на системном диске")
    assert advice in summary
    assert "компания" not in summary
    assert "памя" not in summary


def test_selects_late_relevant_content_beyond_candidate_pool():
    intro = "\n\n".join(f"Печатный каталог содержит сведения о товаре с номером {i}." for i in range(650))
    advice = "Освободите место на системном диске, удалив ненужные временные файлы."
    assert advice in summarize(intro + "\n\n" + advice, "системный диск временные файлы")


def test_query_changes_summary_of_same_page():
    memory = "Закройте неиспользуемые приложения, чтобы уменьшить потребление оперативной памяти."
    disk = "Удалите временные файлы, чтобы освободить место на системном диске."
    content = memory + "\n\n" + disk
    assert memory in summarize(content, "оперативная память приложения", max_sentences=1)
    assert disk in summarize(content, "системный диск временные файлы", max_sentences=1)


def test_noise_html_markdown_links_and_code_are_removed():
    advice = "Удалите временные файлы, чтобы освободить место на системном диске."
    content = f'''<nav>Системный диск предлагает перейти к другим страницам.</nav>
<script>Диск содержит секретный текст скрипта.</script>
<article><p>{advice}</p></article>
<footer>Все права защищены компанией и партнерами.</footer>

Подпишитесь на рассылку о дисках, чтобы получить скидку.

[Системный диск и самые интересные новости.](https://example.test/menu)

```python
Системный диск требует выполнения этого кода.
```
'''
    summary = summarize(content, "системный диск временные файлы")
    assert summary == advice


def test_no_mid_sentence_fragments_or_timestamped_transcripts():
    content = '''[2:23] работе с файловой системой. Следующее, что рекомендуется
оптимизировать - это отключить встроенный антивирус. Вообще,[2:30] что касается
антивируса, здесь очень интересная история. Если вы скачиваете, что попало с
[2:38] разных сайтов, в п'''
    # The timestamps stay attached to their sentences so even capitalized transcript
    # fragments cannot turn into report recommendations.
    summary = summarize(content, "файловая система антивирус")
    assert summary == ""


def test_disjoint_provider_chunks_do_not_form_invented_sentences():
    summary = summarize(
        "Незаконченная фраза о памяти [...] продолжение совершенно другого предложения. "
        "Закройте ненужные приложения для освобождения оперативной памяти. Оборванная рекомендация",
        "оперативная память приложения",
    )
    assert summary == "Закройте ненужные приложения для освобождения оперативной памяти."


def test_related_article_cards_do_not_become_main_page_findings():
    advice = "Удалите временные файлы, чтобы освободить место на системном диске."
    content = f'''Войдите с помощью учетной записи для управления диском.

# Освобождение места

{advice}

## Связанные видеоролики

### Оптимизация дисков

Купите программу для оптимизации дисков и получите специальную скидку.

## Связанные статьи

Системный диск можно ускорить способами из другой статьи.
'''
    assert summarize(content, "системный диск место") == advice


def test_context_and_conditions_are_kept_with_dependent_advice():
    content = ("Если с момента обновления прошло менее десяти дней, сохранена предыдущая версия системы. "
               "Если нужно освободить место на диске, можно удалить ее, потеряв возможность вернуться назад.")
    result = summarize(content, "освободить место на диске")
    assert result == content
    assert summarize(content, "освободить место на диске", max_chars=100) == ""


def test_problem_and_remedy_from_short_paragraph_stay_together():
    content = ("Недостаточное место на диске может привести к снижению производительности системы. "
               "Используйте Контроль памяти и средства очистки, чтобы удалить временные файлы.")
    assert summarize(content, "место на диске производительность") == content


def test_broken_upstream_markup_does_not_leak_into_evidence():
    content = ('''Выберите "Пуск Параметры">Рекомендации по очистке****хранилища>системы>.\n\n'''
               "Удалите временные файлы, чтобы освободить место на системном диске.")
    result = summarize(content, "системный диск очистка")
    assert "Выберите" not in result
    assert "****" not in result and ">" not in result
    assert "Удалите временные файлы" in result


def test_budget_skips_long_sentences_without_cutting_words():
    long = "Системный диск " + "нуждается в очистке " * 40 + "."
    short = "Удалите временные файлы с системного диска."
    summary = summarize(long + " " + short, "системный диск", max_chars=100)
    assert summary == short
    assert len(summary) <= 100
    assert summarize(long, "диск", max_chars=100) == ""


def test_exact_and_near_duplicates_do_not_crowd_out_another_fact():
    first = "Закройте неиспользуемые приложения, чтобы уменьшить потребление оперативной памяти."
    duplicate = "Закройте неиспользуемые приложения, чтобы уменьшить потребление оперативной памяти компьютера."
    other = "Проверьте автозагрузку: фоновые программы занимают оперативную память после запуска системы."
    result = summarize(" ".join([first, first, duplicate, other]), "оперативная память", max_sentences=2)
    assert other in result
    assert sum(s in result for s in (first, duplicate)) == 1


def test_sentence_order_abbreviations_and_decimals_are_preserved():
    first = "В Windows 11 предусмотрен контроль памяти, см. рекомендации в справке."
    second = "При заполнении диска на 95.9% требуется освободить место для временных файлов."
    result = summarize(first + " " + second, "диск память Windows", max_chars=1000)
    assert result == first + " " + second


@pytest.mark.parametrize("content", ["", "# Оперативная память", "Оборванное предложение о памяти",
                                           "Память компьютера ... продолжение отсутствует."])
def test_insufficient_text_does_not_become_evidence(content):
    assert summarize(content, "память") == ""


def test_off_topic_page_is_not_silently_used_as_evidence():
    assert summarize("Рецепт бананового пирога включает муку, сахар и спелые фрукты.",
                     "Windows RAM disk optimization") == ""


def test_no_query_uses_document_content_instead_of_prefix():
    intro = "Наша компания открыла офис для посетителей."
    body = ("Память компьютера хранит данные запущенных приложений. "
            "Оперативная память позволяет программам быстро обращаться к данным. "
            "Недостаток оперативной памяти замедляет выполнение приложений.")
    result = summarize(intro + " " + body, max_sentences=2)
    assert "компания" not in result
    assert "памяти" in result or "память" in result


def test_search_requests_page_text_and_preserves_source_attribution(tavily):
    advice = "Закройте неиспользуемые приложения, чтобы уменьшить потребление оперативной памяти."
    tavily.post.return_value.json.return_value = {"results": [{
        "title": "Память компьютера", "url": "https://support.example.test/performance",
        "content": "Бесполезное вступление к статье", "raw_content": "# Память\n\n" + advice,
    }]}
    result = server.search("оперативная память", include_domains=["support.example.test"],
                           exclude_domains=["youtube.com"])
    payload = tavily.post.call_args.kwargs["json"]
    assert payload["include_raw_content"] == "markdown"
    assert payload["search_depth"] == "advanced"
    assert payload["include_domains"] == ["support.example.test"]
    assert payload["exclude_domains"] == ["youtube.com"]
    assert advice in result
    assert "https://support.example.test/performance" in result
    assert "Отобранные предложения страницы" in result
    assert "вступление" not in result


def test_search_fallback_is_explicit_and_never_uses_broken_snippet(tavily):
    tavily.post.return_value.json.return_value = {"results": [
        {"title": "Память", "url": "https://example.test/complete", "raw_content": None,
         "content": "Закройте неиспользуемые приложения, чтобы уменьшить потребление оперативной памяти. Оборванный хвост"},
        {"title": "Обрывок", "url": "https://example.test/broken", "content": "Память компьютера освобожда"},
    ]}
    result = server.search("оперативная память")
    assert "Фрагмент поисковой выдачи" in result
    assert "Оборванный хвост" not in result
    assert "/broken" not in result


def test_search_skips_duplicate_sources_and_empty_results(tavily):
    entry = {"title": "Очистка диска", "url": "https://example.test/disk",
             "raw_content": "Удалите временные файлы, чтобы освободить место на системном диске."}
    tavily.post.return_value.json.return_value = {"results": [entry, {**entry, "url": entry["url"] + "/#top"}]}
    assert server.search("системный диск").count("Источник:") == 1
    tavily.post.return_value.json.return_value = {"results": []}
    assert server.search("диск") == "No results found."


def test_search_does_not_pad_report_with_irrelevant_or_empty_evidence(tavily):
    tavily.post.return_value.json.return_value = {"results": [{
        "url": "https://example.test/", "content": "Незаконченная фраза", "raw_content": "# Меню",
    }]}
    assert "сведений для рекомендаций недостаточно" in server.search("память")


def test_page_extract_uses_relevant_late_content_and_respects_budget(tavily):
    intro = "Эта компания работает в нескольких странах мира. " * 100
    advice = "Освободите место на системном диске, удалив ненужные временные файлы."
    tavily.post.return_value.json.return_value = {"results": [{"raw_content": intro + "\n\n" + advice}]}
    result = server.get_page_text("https://example.test/disk", "системный диск", max_chars=200)
    assert advice in result
    assert "компания" not in result
    assert "https://example.test/disk" in result
    assert len(result.split("Отобранные предложения страницы:\n\n")[1]) <= 200


def test_page_without_usable_evidence_is_an_error(tavily):
    tavily.post.return_value.json.return_value = {"results": [{"raw_content": "Оборванная фраза"}]}
    with pytest.raises(ToolError) as exc:
        server.get_page_text("https://example.test/")
    assert tool_result_error(str(exc.value)).code == "extraction_failed"


@pytest.mark.parametrize("call", [
    lambda: server.search(" "), lambda: server.search("topic", max_results=21),
    lambda: server.search("topic", include_domains=["https://example.test/path"]),
    lambda: server.get_page_text("file:///private"),
    lambda: server.get_page_text("https://example.test", max_chars=199),
])
def test_invalid_arguments_do_not_make_network_requests(tavily, call):
    with pytest.raises(ToolError) as exc:
        call()
    assert tool_result_error(str(exc.value)).code == "invalid_arguments"
    tavily.post.assert_not_called()
