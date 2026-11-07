"""Manual browser QA: real API/UI, deterministic planner, no external calls/actions.

Run: python -m tests.ui_preview_server
Tasks: any text = parallel DAG; 'Сохрани текст в файл' = elicitation;
'Ошибка файла' = failed dependency; 'Проверка лимита' = planner failure.
"""
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from orchestrator import main
from orchestrator.errors import AppError


def fake_plan(task, description, history):
    if 'лимит' in task.lower():
        raise AppError('rate_limited', 'Rate limit exceeded')
    if 'сохрани текст' in task.lower() and not history:
        # Reproduce the real regression: opaque IDs and English model questions.
        return {'questions': [{'id': 'q1', 'question': 'What text would you like to save?'},
                              {'id': 'q2', 'question': 'Where should the file be saved?'}]}
    if 'сохрани текст' in task.lower():
        return {'steps': [{'id': '1', 'server': 'filesystem', 'tool': 'write_file',
                           'params': {'path': history[1]['answer'], 'content': history[0]['answer']},
                           'depends_on': [], 'description': 'Сохранить файл'}]}
    if 'ошибка' in task.lower():
        return {'steps': [
            {'id': '1', 'server': 'filesystem', 'tool': 'read_file', 'params': {'path': 'data/definitely_missing_file.txt'}, 'depends_on': [], 'description': 'Прочитать исходный файл'},
            {'id': '2', 'server': 'filesystem', 'tool': 'write_file', 'params': {'path': 'data/copy.txt', 'content': '{{step_1.result}}'}, 'depends_on': ['1'], 'description': 'Сохранить копию'},
        ]}
    if 'большой граф' in task.lower():
        steps = [
            {'id': str(index), 'server': 'sysinfo', 'tool': 'get_cpu' if index % 2 else 'get_memory',
             'params': {}, 'depends_on': [], 'description': f'Получить сведения {index}'}
            for index in range(1, 5)
        ]
        for sid, deps in [('5', ['1']), ('6', ['2']), ('7', ['3']), ('8', ['4']),
                          ('9', ['5', '8']), ('10', ['6', '7']), ('11', ['9', '10', '1']),
                          ('12', ['5', '6']), ('13', ['11', '12'])]:
            steps.append({'id': sid, 'server': 'filesystem', 'tool': 'write_file',
                          'params': {'path': f'data/preview_{sid}.txt', 'content': '\n'.join('{{step_' + dep + '.result}}' for dep in deps)},
                          'depends_on': deps, 'description': f'Подготовить материал {sid}'})
        return {'steps': steps}
    return {'steps': [
        {'id': '1', 'server': 'sysinfo', 'tool': 'get_cpu', 'params': {}, 'depends_on': [], 'description': 'Получить сведения о процессоре'},
        {'id': '2', 'server': 'sysinfo', 'tool': 'get_memory', 'params': {}, 'depends_on': [], 'description': 'Получить сведения о памяти'},
        {'id': '3', 'server': 'filesystem', 'tool': 'write_file', 'params': {'path': 'data/report.txt', 'content': '{{step_1.result}}\n{{step_2.result}}'}, 'depends_on': ['1', '2'], 'description': 'Сохранить отчёт'},
    ]}


async def fake_execute(plan, on_event=None, **kwargs):
    log = []
    for step in plan['steps']:
        entry = {**step, 'status': 'running', 'attempts': 1, 'started_at': 1}
        await on_event({'type': 'step_update', **entry})
        await asyncio.sleep(1.5)
        if step['tool'] == 'read_file':
            entry.update(status='error', error_code='not_found', error='File not found', duration_ms=1500)
        elif any(item['status'] == 'error' for item in log):
            entry.update(status='skipped', error_code='dependency_failed', error='Dependency failed', blocked_by=['1'], attempts=0, duration_ms=0)
        else:
            entry.update(status='done', result='Проверочные данные. Настоящие действия в этом режиме не выполняются.', duration_ms=1500)
        log.append(entry)
        await on_event({'type': 'step_update', **entry})
    return log


@asynccontextmanager
async def preview_lifespan(app):
    main._tools = [
        {'server': 'sysinfo', 'name': name, 'input_schema': {'type': 'object', 'properties': {}}}
        for name in ('get_cpu', 'get_memory')
    ] + [
        {'server': 'filesystem', 'name': 'read_file', 'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}},
        {'server': 'filesystem', 'name': 'write_file', 'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['path', 'content']}},
    ]
    yield


if __name__ == '__main__':
    main.plan = fake_plan
    main.execute = fake_execute
    main.app.router.lifespan_context = preview_lifespan
    uvicorn.run(main.app, host='127.0.0.1', port=8765)
