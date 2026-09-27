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
STEP_MAX_RETRIES=3                   # кол-во попыток при ошибке
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
