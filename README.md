# Planner Agent

**Intelligent system for adaptive planning and execution of composite user tasks**

Система принимает задачу на естественном языке, строит план выполнения в виде ориентированного ациклического графа (DAG) с помощью LLM и исполняет его через MCP-серверы с параллельным выполнением независимых шагов.

## Архитектура

```
Browser (Bootstrap UI)
        │  WebSocket / REST
FastAPI Orchestrator
  ├── Planner   — LLM → JSON DAG
  ├── Executor  — asyncio DAG runner (параллельные ветки)
  └── Registry  — автообнаружение инструментов
        │  MCP stdio
  ┌─────┴──────────────────────────────────┐
  │  filesystem │ notes │ reports │ sysinfo │ email │ websearch  │
  └────────────────────────────────────────┘
```

## Требования

- Python 3.11+
- Google Gemini API key (бесплатно на [aistudio.google.com](https://aistudio.google.com))
- Опционально: Tavily API key (для websearch), Gmail App Password (для email)

## Установка

```bash
git clone <repo>
cd planner
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -e .
```

## Конфигурация

Скопируй `.env.example` → `.env` и заполни:

```env
LLM_API_KEY=ваш_gemini_ключ
LLM_MODEL=gemini-3.6-flash
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/

TAVILY_API_KEY=ваш_tavily_ключ      # websearch (необязательно)
EMAIL_ADDRESS=you@gmail.com          # email (необязательно)
EMAIL_PASSWORD=app_password          # Gmail App Password

STEP_TIMEOUT_SEC=30                  # таймаут шага (по умолчанию 30 с)
STEP_MAX_RETRIES=3                   # максимум попыток временного сбоя чтения
PLANNING_TIMEOUT_SEC=60              # таймаут ответа планировщика
ELICITATION_TIMEOUT_SEC=300          # время на ответ пользователя
LOG_LEVEL=INFO
LOG_FILE=logs/planner.log
```

## Запуск

```bash
uvicorn orchestrator.main:app --port 8000
```

Открой **http://localhost:8000** в браузере.

### Docker

```bash
docker compose up
```

## API

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/` | Web UI |
| `WS`  | `/ws` | WebSocket: отправь `{"task":"..."}`, получай события в реальном времени |
| `POST` | `/run` | REST: `{"task":"..."}` → полный JSON-ответ |
| `GET` | `/tools` | Список всех обнаруженных инструментов |
| `GET` | `/health` | Статус системы |

### WebSocket события

```json
{"type": "plan",        "plan": {...}}
{"type": "step_update", "id": "1", "status": "running", ...}
{"type": "step_update", "id": "1", "status": "done",    "result": "...", "duration_ms": 1234}
{"type": "done",        "success": true, "execution": [...]}
```

### Уточнение недостающих данных (elicitation)

Если задача не содержит необходимых данных, например «сохрани текст в файл» без
текста и пути, планировщик возвращает уточняющие вопросы. В браузере появляется
форма с кнопками Continue и Decline. Ответ передаётся в текущий запуск, после чего
строится окончательный план. До получения ответов инструменты не выполняются.
Обязательные параметры дополнительно проверяются по схемам обнаруженных инструментов:
если планировщик пропустил параметр, система также запрашивает его через форму.
Точность распознавания смысловой неоднозначности зависит от планировщика;
проверка отсутствующих обязательных параметров выполняется программно.

Протокол WebSocket:

```json
{"type":"elicitation","request_id":"...","message":"Куда сохранить файл?","requested_schema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}
{"type":"elicitation_response","request_id":"...","action":"accept","content":{"path":"data/result.txt"}}
{"type":"input_accepted","request_id":"..."}
```

Вместо `accept` можно отправить `decline` или `cancel`, чтобы отказаться от ответа.
Ошибочный ответ даёт `input_error`, а форма остаётся доступной для исправления.
`{"type":"cancel"}` отменяет весь запуск; при разрыве соединения запуск также
отменяется. Одно соединение поддерживает последовательные задачи и несколько
уточнений. Запросы различаются по `request_id`, в том числе у параллельных шагов.
Поддерживаются формы со строками, числами, целыми числами, логическими значениями
и перечислениями этих типов. Остальные формы завершаются явной ошибкой.

Поддержаны также запросы MCP `elicitation/create` во время выполнения инструмента:
шаг получает состояние `waiting_input`, время ответа пользователя не расходует
таймаут инструмента. Для таких запросов требуется WebSocket. Через `/run` запрос
ввода во время выполнения даёт ошибку шага `elicitation_required`.

REST `/run` при нехватке данных на этапе планирования возвращает HTTP 200,
`status: "needs_input"`, `success: false`, пустой `execution` и описание формы в
`elicitation.requested_schema`. Чтобы продолжить, нужно повторить исходную задачу,
добавив ответы в `clarifications` (сохранив предыдущие уточнения):

```json
{"task":"сохрани hello в файл","clarifications":[{"question":"Куда сохранить файл?","answer":"data/hello.txt"}]}
```

### Ошибки выполнения

Перед запуском проверяется весь граф: уникальность шагов, существование зависимостей,
отсутствие циклов, ссылки на результаты, доступность инструментов и параметры.
Пустой план не считается успешным. После подстановки результатов типы параметров
проверяются повторно. Ошибки планирования и сервисов возвращаются как JSON
с `code`, `message`, `retryable` и `run_id`; неверный план даёт HTTP 422,
сбой сервиса планирования — HTTP 503. Ошибка отдельного инструмента отражается
в итоговом `execution` и `success: false`.

MCP-ответ с `isError: true` никогда не передаётся дальше как полезные данные.
Например, при чтении отсутствующего файла узел становится красным (`error`,
`error_code: "not_found"`), запись копии и последующие зависимые узлы пропускаются
(`skipped`, `dependency_failed`, `blocked_by`). Независимые ветки продолжают работу.
Пустой файл и текст, содержащий слово `error`, остаются корректными результатами.

Предусмотрены коды для ошибок доступа, пути, кодировки, конфигурации,
аутентификации, ограничений сервиса, сети и таймаутов. Постоянные ошибки не
повторяются. Временные сетевые ошибки повторяются только у известных операций
чтения. Отправка писем и другие действия с побочными эффектами автоматически не
повторяются. После таймаута результат внешнего действия может быть неизвестен.
Отмена и ошибки не откатывают уже завершённые действия. Текстовые файлы и отчёты
заменяются атомарно: ошибка формирования не должна портить существующий файл.

### Логирование

Приложение пишет JSON-события в stderr и `logs/planner.log`. По достижении 5 МБ
файл ротируется, хранятся три резервные копии. Путь и уровень задаются через
`LOG_FILE` и `LOG_LEVEL`. Каталог логов исключён из Git. При недоступности файла
журнал продолжает выводиться в stderr.

Фиксируются запуск приложения, обнаружение инструментов, планирование,
запросы ввода, начало и конец шагов, попытки, длительности, коды ошибок и итог
задачи. `run_id` связывает события одного запуска, `step_id` — одного шага.
Идентификатор запуска также возвращается в API и передаётся в событии
`run_started`. Тексты задач, ответы пользователя, параметры и результаты
инструментов в журнал не записываются.

## MCP-серверы

| Сервер | Инструменты |
|--------|-------------|
| `filesystem` | `read_file`, `write_file`, `list_dir` |
| `notes` | `create_note`, `get_note`, `search_notes`, `list_notes` |
| `reports` | `create_pdf`, `create_docx`, `create_pdf_with_table` |
| `sysinfo` | `get_system_info`, `get_cpu`, `get_memory`, `get_disk`, `get_top_processes` |
| `email` | `send_email`, `send_email_with_attachment` |
| `websearch` | `search`, `get_page_text` |

## Тесты

```bash
pip install -e ".[test]"
pytest tests/ -v
```

## Бенчмарк

```bash
python -m scripts.benchmark
```

## Примеры задач

```
get CPU and memory info simultaneously, then write data/sysreport.txt
search the web for "Python 3.14 features" and save a note titled Python News
get full system info and create a PDF report at data/system_report.pdf
list files in the data directory and save results to data/listing.txt
```
