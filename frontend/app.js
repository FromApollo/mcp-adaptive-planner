import {applyEvent, closeRun, isActive, loadHistory, newRun, progress, removeRun, restoreRun, saveHistory} from './state.js';
import {dateLabel, duration, errorText, esc, fieldLabel, icon, STATUS, statusPill, stepTitle, textValue} from './ui.js';
import {DagView} from './dag-view.js';
import {renderMarkdown} from './markdown.js';

const $ = id => document.getElementById(id);
const storage = {getItem: key => localStorage.getItem(key), setItem: (key, value) => localStorage.setItem(key, value)};
const loaded = loadHistory(storage);
const runs = loaded.runs;
let selectedId = runs[0]?.id || null;
let activeId = null;
let socket = null;
let socketOwnerId = null;
let connecting = null;
let deleted = null;
let formVersion = '';
let persistTimer;
const expanded = new Map();
const dagView = new DagView($('dag-container'), $('dag-toolbar'));
const EXAMPLES = {
  system: 'Получи сведения о процессоре и памяти параллельно, затем сохрани общий отчёт в data/report.txt.',
  note: 'Создай заметку с названием «План на день» и текстом «Проверить работу системы».',
  copy: 'Прочитай файл data/report.txt и сохрани его содержимое в data/report_copy.txt.',
};

function findRun(id) { return runs.find(run => run.id === id); }
function activeRun() { return findRun(activeId); }
function selectedRun() { return findRun(selectedId); }
function announce(message) { $('live-status').textContent = message; }
function showStorageWarning(message) {
  $('storage-warning').hidden = !message;
  $('storage-warning').textContent = message || '';
}
function persist() { clearTimeout(persistTimer); showStorageWarning(saveHistory(storage, runs)); }
function notify(message, undo = false) {
  $('notification-text').textContent = message;
  $('undo-delete').hidden = !undo;
  bootstrap.Toast.getOrCreateInstance($('notification'), {delay: 7000}).show();
}
function hideSidebar() { bootstrap.Offcanvas.getInstance($('history-sidebar'))?.hide(); }

function renderHistory() {
  $('history-count').textContent = runs.length;
  $('clear-history-btn').disabled = !runs.some(run => run.id !== activeId);
  $('history-list').innerHTML = runs.length ? runs.map(run => `
    <div class="history-item ${run.id === selectedId ? 'history-item--active' : ''}">
      <button class="history-main" type="button" data-action="select-run" data-id="${esc(run.id)}" ${run.id === selectedId ? 'aria-current="true"' : ''}>
        <span class="history-title" title="${esc(run.prompt)}">${run.id === activeId ? '<span class="active-inline-dot"></span>' : ''}${esc(run.prompt)}</span>
        <span class="history-meta">${statusPill(run.status)}<time datetime="${esc(new Date(run.createdAt).toISOString())}">${esc(dateLabel(run.createdAt))}</time></span>
      </button>
      <button class="delete-button" type="button" data-action="delete-run" data-id="${esc(run.id)}" aria-label="Удалить из истории: ${esc(run.prompt)}" title="${run.id === activeId ? 'Активную задачу нельзя удалить' : 'Удалить из истории'}" ${run.id === activeId ? 'disabled' : ''}>${icon('trash', 16)}</button>
    </div>`).join('') : `<div class="empty-history"><div class="empty-icon">${icon('clock', 22)}</div><strong>История пока пуста</strong><p>Здесь появятся ваши запуски.</p></div>`;
}

function renderActive() {
  const run = activeRun();
  $('run-btn').disabled = Boolean(run) || !$('task-prompt').value.trim();
  $('stop-btn').disabled = !run || run.status === 'cancelling';
  $('composer-note').hidden = !run;
  $('active-run-card').innerHTML = run ? `<button class="active-run-card" type="button" data-action="select-run" data-id="${esc(run.id)}"><span class="active-run-dot"></span><span><small>${STATUS[run.status]?.[0] || 'Текущая задача'}</small><strong>${esc(run.prompt)}</strong></span></button>` : '';
  $('active-run-banner').innerHTML = run && selectedId !== run.id ? `<div class="active-run-banner"><div>${statusPill(run.status)}<span>${esc(run.prompt)}</span></div><button class="btn secondary-button" type="button" data-action="select-run" data-id="${esc(run.id)}">Вернуться к текущей задаче</button></div>` : '';
}

function elapsed(run) {
  return run.legacy ? run.previousDurationMs : (run.endedAt ?? Date.now()) - run.createdAt;
}
function progressLabel(run) {
  const {done, total} = progress(run);
  const noun = total % 10 === 1 && total % 100 !== 11 ? 'шага' : 'шагов';
  return total ? `Выполнено ${done} из ${total} ${noun}` : run.legacy ? 'Подробности не сохранены' : 'План ещё не составлен';
}
function summary(run) {
  if (run.status === 'planning') return 'Составляется план выполнения задачи…';
  if (run.status === 'waiting_input') return 'Для продолжения необходимо ответить на уточнение.';
  if (run.status === 'running') return 'Выполнение продолжается. Результаты поступают по мере готовности.';
  if (run.status === 'done') return 'Все шаги успешно выполнены.';
  if (run.status === 'cancelling') return 'Ожидается остановка выполнения…';
  if (run.status === 'cancelled') return 'Выполнение остановлено пользователем.';
  if (run.status === 'interrupted') return 'Соединение с прошлым запуском потеряно.';
  return 'Не удалось выполнить задачу полностью.';
}

function renderSelected(reset = false) {
  const run = selectedRun();
  $('run-view').hidden = !run;
  $('welcome').hidden = Boolean(run);
  if (!run) { dagView.clear(); formVersion = ''; $('clarifications').replaceChildren(); return; }
  if (reset) formVersion = '';
  $('run-heading').innerHTML = `<div class="run-heading"><div><div class="run-heading-top">${statusPill(run.status)}<span class="run-date">${esc(dateLabel(run.createdAt))}</span>${run.id === activeId ? '<span class="active-label">Текущая задача</span>' : ''}</div><h2>${esc(run.prompt)}</h2><p>${summary(run)}</p></div><button class="btn secondary-button" type="button" data-action="repeat">${icon('repeat', 17)}Повторить задачу</button></div>`;
  $('run-meta').innerHTML = `<div class="run-meta-row"><span>Идентификатор запуска</span><code>${esc(run.serverRunId || run.id)}</code><button type="button" data-action="copy-id" aria-label="Копировать идентификатор запуска">${icon('copy', 15)}Копировать</button></div>`;
  $('run-prompt').textContent = run.prompt;
  $('run-duration').textContent = duration(elapsed(run));
  $('run-progress').textContent = $('plan-progress').textContent = progressLabel(run);
  renderClarifications(run);
  renderAlert(run);
  $('legacy-notice').innerHTML = run.legacy ? '<div class="alert alert-secondary small">Это запись из предыдущей версии. Сохранилась общая статистика, но план и журнал шагов для неё недоступны.</div>' : '';
  if (run.steps.length) dagView.render(run.steps, run.id);
  else {
    dagView.clear();
    $('dag-container').innerHTML = `<div class="dag-empty">${icon(run.status === 'planning' ? 'loader' : 'spark', 22)}<strong>${run.status === 'planning' ? 'Составляется план…' : run.status === 'waiting_input' ? 'Ожидается уточнение задачи' : 'План не был создан'}</strong><span>${run.legacy ? 'Эти данные не сохранялись в старой версии.' : run.status === 'planning' ? 'После подготовки здесь появятся шаги и зависимости.' : 'Шаги пока не запускались.'}</span></div>`;
  }
  renderLog(run);
}

function renderAlert(run) {
  const failedStep = run.steps.find(step => step.status === 'error');
  const code = run.errorCode && run.errorCode !== 'execution_failed' ? run.errorCode : failedStep?.error_code;
  if (['error', 'interrupted', 'cancelled'].includes(run.status)) {
    const mayHaveRun = run.steps.some(step => (step.attempts || 0) > 0 || step.started_at || step.status === 'done');
    $('run-alert').innerHTML = `<div class="error-banner ${run.status === 'cancelled' ? 'notice-neutral' : ''}" role="status">${icon(run.status === 'cancelled' ? 'stop' : 'alert', 20)}<div><strong>${run.status === 'cancelled' ? 'Задача остановлена' : run.status === 'interrupted' ? 'Выполнение прервано' : 'Не удалось выполнить задачу'}</strong><p>${esc(errorText(code, run.error || failedStep?.error))}</p>${!mayHaveRun && !run.legacy && run.status !== 'interrupted' ? '<b>Действия не выполнялись.</b>' : ''}</div></div>`;
  } else if (run.status === 'done') {
    $('run-alert').innerHTML = `<div class="result-banner" role="status"><div class="result-icon">${icon('check', 20)}</div><div><span>Результат</span><strong>Задача выполнена. Результаты каждого шага доступны в журнале ниже.</strong></div></div>`;
  } else $('run-alert').replaceChildren();
}

function renderLog(run) {
  if (!expanded.has(run.id)) expanded.set(run.id, new Set());
  const opened = expanded.get(run.id);
  $('log-container').innerHTML = run.steps.length ? run.steps.map(step => {
    const status = STATUS[step.status] ? step.status : 'pending';
    const result = Object.hasOwn(step, 'result') ? (textValue(step.result) || '(Пустой результат)')
      : step.error ? errorText(step.error_code, step.error) : 'Результат ещё не получен.';
    const formattedResult = typeof step.result === 'string'
      ? `<div class="markdown-result">${renderMarkdown(result)}</div><details class="raw-result"><summary>Исходный текст</summary><pre>${esc(result)}</pre></details>`
      : `<pre>${esc(result)}</pre>`;
    const fact = (title, value, wide = false) => `<div class="${wide ? 'fact-wide' : ''}"><dt>${title}</dt><dd>${value}</dd></div>`;
    const index = run.steps.indexOf(step);
    return `<article class="step step--${status}" id="step-${index}"><button class="step-summary" type="button" data-action="toggle-step" data-id="${esc(step.id)}" aria-expanded="${opened.has(step.id)}" aria-controls="step-details-${index}"><span class="step-icon">${icon(STATUS[status][1], 17)}</span><span class="step-text"><strong>${esc(stepTitle(step))}</strong><small>Шаг ${esc(step.id)} · ${STATUS[status][0]}</small></span><span class="step-duration">${duration(step.duration_ms)}</span><span class="chevron ${opened.has(step.id) ? 'chevron--open' : ''}">${icon('chevron', 16)}</span></button>
      <div id="step-details-${index}" class="step-details ${step.status === 'error' ? 'step-details--error' : ''}" ${opened.has(step.id) ? '' : 'hidden'}><dl class="step-facts">
        ${fact('Полный результат', formattedResult, true)}
        ${fact('Инструмент', `<code>${esc(step.server)}.${esc(String(step.tool).split('.').pop())}</code>`)}
        ${fact('Длительность', duration(step.duration_ms))}${fact('Попытки', esc(step.attempts ?? 0))}
        ${fact('Код ошибки', `<code>${esc(step.error_code || '—')}</code>`)}
        ${fact('Зависимости', step.depends_on?.length ? step.depends_on.map(id => `Шаг ${esc(id)}`).join(', ') : 'Нет, шаг независимый')}
        ${step.blocked_by?.length ? fact('Причина пропуска', `Не выполнены зависимости: ${step.blocked_by.map(id => `шаг ${esc(id)}`).join(', ')}.`, true) : ''}
        ${step.error ? fact('Техническое сообщение', `<pre>${esc(step.error)}</pre>`, true) : ''}
      </dl></div></article>`;
  }).join('') : '<div class="log-empty">Шаги не запускались.</div>';
}

function renderClarifications(run) {
  // Do not rebuild a live form on every step event: keep focus and typed answers.
  const version = JSON.stringify([run.id, activeId, run.clarifications.map(q => [q.id, q.state, q.error])]);
  if (version === formVersion) return;
  formVersion = version;
  const focused = document.activeElement;
  const focusedRequest = focused?.closest('.answer-form')?.dataset.requestId;
  const focusedField = focused?.dataset.fieldIndex;
  const selection = focused?.selectionStart;
  $('clarifications').innerHTML = run.clarifications.map((request, requestIndex) => {
    const live = run.id === activeId && ['pending', 'submitting'].includes(request.state);
    const busy = request.state === 'submitting' || run.status === 'cancelling';
    const fields = Object.entries(request.schema.properties || {});
    const fieldHTML = fields.map(([key, prop], index) => {
      const id = `answer-${requestIndex}-${index}`;
      const value = Object.hasOwn(request.values, key) ? request.values[key] : prop.default;
      const required = request.schema.required?.includes(key);
      const label = esc(fieldLabel(key, prop.title, prop.description));
      const attrs = `id="${id}" data-field-index="${index}" ${live ? '' : 'disabled'} ${busy ? 'disabled' : ''}`;
      let control;
      if (prop.enum) {
        control = `<select class="form-select" ${attrs} ${required ? 'required' : ''}><option value="">Выберите значение</option>${prop.enum.map(option => `<option value="${esc(JSON.stringify(option))}" ${option === value ? 'selected' : ''}>${esc(option)}</option>`).join('')}</select>`;
      } else if (prop.type === 'boolean') {
        control = `<input class="form-check-input" type="checkbox" ${attrs} ${value === true ? 'checked' : ''}>`;
      } else {
        const numeric = ['number', 'integer'].includes(prop.type);
        control = `<input class="form-control" type="${numeric ? 'number' : 'text'}" ${attrs} value="${esc(value ?? '')}" ${required && live ? 'required' : ''} ${numeric ? `step="${prop.type === 'integer' ? '1' : 'any'}"` : ''} ${prop.minimum != null ? `min="${esc(prop.minimum)}"` : ''} ${prop.maximum != null ? `max="${esc(prop.maximum)}"` : ''} ${prop.minLength != null ? `minlength="${esc(prop.minLength)}"` : ''} ${prop.maxLength != null ? `maxlength="${esc(prop.maxLength)}"` : ''}>`;
      }
      return `<div class="answer-field ${prop.type === 'boolean' && !prop.enum ? 'boolean-answer' : ''}"><label class="field-label" for="${id}">${label}${required ? '<span class="required-mark"> *</span>' : ''}</label>${control}<span class="field-error" id="${id}-error" aria-live="polite"></span></div>`;
    }).join('');
    const archiveText = request.state === 'answered' ? `Ответ получен ${dateLabel(request.answeredAt)}`
      : request.state === 'declined' ? 'Пользователь отказался от уточнения.' : 'Запрос закрыт. Выполнение не возобновляется.';
    return `<section class="clarification card ${live ? '' : 'clarification--archive'}"><div class="clarification-icon">${icon(live ? 'help' : 'check', 21)}</div><div class="clarification-content"><h3 class="small-heading">${live ? 'Нужно уточнение' : 'Уточнение задачи'}</h3><p>${esc(request.message)}</p><form class="answer-form" data-request-id="${esc(request.id)}" novalidate><div class="fields-row fields-row--dynamic">${fieldHTML}</div>${request.error ? `<p class="field-error" role="alert">${esc(errorText(request.errorCode, request.error))}</p>` : ''}${live ? `<div class="clarification-actions"><button class="btn btn-primary primary-button" type="submit" ${busy ? 'disabled' : ''}>${busy ? 'Ожидание…' : 'Продолжить'}</button><button class="btn secondary-button" type="button" data-action="decline" data-id="${esc(request.id)}" ${busy ? 'disabled' : ''}>Отказаться</button></div>` : `<p class="archive-note">${esc(archiveText)} Только просмотр.</p>`}</form></div></section>`;
  }).join('');
  if (focusedRequest && focusedField !== undefined) {
    const replacement = $('clarifications').querySelector(
      `form[data-request-id="${CSS.escape(focusedRequest)}"] [data-field-index="${CSS.escape(focusedField)}"]`,
    );
    if (replacement && !replacement.disabled) {
      replacement.focus({preventScroll: true});
      if (replacement.type === 'text' && selection !== null) replacement.setSelectionRange(selection, selection);
    }
  }
}

function render(reset = false) { renderHistory(); renderActive(); renderSelected(reset); }
function chooseRun(id) { if (!findRun(id)) return; selectedId = id; render(true); hideSidebar(); }
function newTask(prompt = '') {
  selectedId = null;
  $('task-prompt').value = prompt;
  render(true);
  hideSidebar();
  $('task-prompt').focus();
}

function finish(run, status, code, message) {
  closeRun(run, status, code, message);
  if (activeId === run.id) activeId = null;
  persist(); render(); announce(summary(run));
}
function receive(socketInstance, raw) {
  if (socket !== socketInstance) return;
  const run = findRun(socketOwnerId);
  if (!run) return;
  try {
    const event = JSON.parse(raw);
    if (!event || typeof event.type !== 'string') throw new Error('Invalid event');
    if (!isActive(run) && !['input_closed', 'input_accepted'].includes(event.type)) return;
    applyEvent(run, event);
    if (!isActive(run) && activeId === run.id) activeId = null;
    if (event.type === 'input_error' && !event.request_id) notify(errorText(event.code, event.message));
    persist(); render();
    if (['done', 'error', 'cancelled', 'elicitation'].includes(event.type)) announce(summary(run));
    if (event.type === 'elicitation' && selectedId === run.id) $('clarifications').querySelector('input, select')?.focus({preventScroll: true});
  } catch {
    if (isActive(run)) finish(run, 'error', 'internal_error', 'Сервер вернул некорректное сообщение.');
    socketInstance.close();
  }
}

function setConnection(state) {
  $('connection').classList.toggle('connection--offline', state === 'offline');
  $('connection').classList.toggle('connection--connecting', state === 'connecting');
  $('connection-label').textContent = {online: 'Подключено', offline: 'Нет соединения · подключить', connecting: 'Подключение…'}[state];
}
function connect() {
  if (socket?.readyState === WebSocket.OPEN) return Promise.resolve(socket);
  if (connecting) return connecting;
  setConnection('connecting');
  const current = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  socket = current;
  connecting = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { reject(new Error('Connection timeout')); current.close(); }, 10000);
    current.onopen = () => { clearTimeout(timeout); setConnection('online'); resolve(current); };
    current.onmessage = event => receive(current, event.data);
    current.onerror = () => { clearTimeout(timeout); reject(new Error('Connection failed')); };
    current.onclose = () => {
      clearTimeout(timeout); reject(new Error('Connection closed'));
      if (socket !== current) return;
      socket = null; setConnection('offline');
      const run = findRun(socketOwnerId);
      if (run && isActive(run)) finish(run, run.sent ? 'interrupted' : 'error', run.sent ? 'connection_lost' : 'connection_failed', null);
    };
  }).finally(() => { connecting = null; });
  return connecting;
}

async function startTask() {
  if (activeRun()) { notify('Сначала дождитесь завершения текущей задачи.'); return; }
  const prompt = $('task-prompt').value.trim();
  if (!prompt) { $('task-prompt').focus(); return; }
  const run = newRun(prompt, crypto.randomUUID());
  runs.unshift(run); activeId = selectedId = run.id;
  socketOwnerId = run.id;
  persist(); render(true); announce('Составляется план выполнения.');
  try {
    const channel = await connect();
    if (activeId !== run.id || !isActive(run)) return;
    channel.send(JSON.stringify({task: prompt}));
    run.sent = true;
  } catch {
    if (isActive(run)) finish(run, 'error', 'connection_failed', null);
  }
}
function stopTask() {
  const run = activeRun();
  if (!run || run.status === 'cancelling') return;
  if (!run.sent) { finish(run, 'cancelled', 'cancelled', null); return; }
  if (socket?.readyState !== WebSocket.OPEN) { finish(run, 'interrupted', 'connection_lost', null); return; }
  socket.send(JSON.stringify({type: 'cancel'}));
  run.status = 'cancelling'; formVersion = ''; persist(); render();
}

function collectAnswers(form, request) {
  const answers = Object.create(null);
  let valid = true;
  Object.entries(request.schema.properties || {}).forEach(([key, prop], index) => {
    const input = form.querySelector(`[data-field-index="${index}"]`);
    let message = '';
    const required = request.schema.required?.includes(key);
    if (required && prop.type !== 'boolean' && !input.value.trim()) message = 'Заполните это поле.';
    else if (!input.validity.valid) message = 'Проверьте формат и допустимое значение.';
    input.setAttribute('aria-invalid', String(Boolean(message)));
    input.setAttribute('aria-describedby', `${input.id}-error`);
    $(`${input.id}-error`).textContent = message;
    if (message) valid = false;
    if (input.value === '' && !required && (prop.type !== 'boolean' || prop.enum)) return;
    answers[key] = prop.enum ? (input.value ? JSON.parse(input.value) : null)
      : prop.type === 'boolean' ? input.checked : ['number', 'integer'].includes(prop.type) ? Number(input.value) : input.value;
  });
  if (!valid) { form.querySelector('[aria-invalid="true"]')?.focus(); return null; }
  return answers;
}
function respond(requestId, action, form) {
  const run = activeRun();
  const request = run?.clarifications.find(item => item.id === requestId);
  if (!request || request.state !== 'pending' || selectedId !== run.id || run.status === 'cancelling') return;
  if (socket?.readyState !== WebSocket.OPEN) { finish(run, 'interrupted', 'connection_lost', null); return; }
  const content = action === 'accept' ? collectAnswers(form, request) : undefined;
  if (action === 'accept' && content === null) return;
  if (content) request.values = content;
  request.error = null;
  request.state = action === 'accept' ? 'submitting' : 'declined';
  socket.send(JSON.stringify({type: 'elicitation_response', request_id: requestId, action, content}));
  persist(); render();
}

// One delegated handler for small view actions, rather than per-render listeners.
document.addEventListener('click', async event => {
  const example = event.target.closest('[data-example]');
  if (example) { $('task-prompt').value = EXAMPLES[example.dataset.example]; renderActive(); $('task-prompt').focus(); return; }
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled) return;
  const id = button.dataset.id;
  switch (button.dataset.action) {
    case 'new-task': newTask(); break;
    case 'select-run': chooseRun(id); break;
    case 'repeat': newTask(selectedRun()?.prompt || ''); break;
    case 'stop': stopTask(); break;
    case 'reconnect': connect().catch(() => notify('Не удалось подключиться к серверу.')); break;
    case 'delete-run': {
      const removed = removeRun(runs, id, activeId);
      if (!removed) return;
      deleted = removed;
      if (selectedId === id) selectedId = null;
      persist(); render(true); hideSidebar(); notify('Запуск удалён из истории. Созданные файлы сохранены.', true);
      break;
    }
    case 'undo-delete':
      restoreRun(runs, deleted); deleted = null; persist(); render(true);
      bootstrap.Toast.getInstance($('notification'))?.hide(); break;
    case 'confirm-clear': {
      const panel = $('history-sidebar');
      const show = () => bootstrap.Modal.getOrCreateInstance($('clear-modal')).show();
      if (panel.classList.contains('show')) {
        panel.addEventListener('hidden.bs.offcanvas', show, {once: true});
        hideSidebar();
      } else show();
      break;
    }
    case 'clear-history':
      for (let index = runs.length - 1; index >= 0; index--) if (runs[index].id !== activeId) runs.splice(index, 1);
      deleted = null; if (selectedId !== activeId) selectedId = null;
      bootstrap.Modal.getInstance($('clear-modal'))?.hide();
      persist(); render(true); notify('История очищена. Текущая задача и созданные файлы сохранены.'); break;
    case 'copy-id':
      try { await navigator.clipboard.writeText(selectedRun().serverRunId || selectedRun().id); notify('Идентификатор скопирован.'); }
      catch { notify('Не удалось скопировать. Выделите идентификатор и скопируйте вручную.'); }
      break;
    case 'toggle-step':
    case 'focus-step': {
      const run = selectedRun();
      const opened = expanded.get(run.id) || new Set();
      const focus = button.dataset.action === 'focus-step';
      if (opened.has(id) && !focus) opened.delete(id); else opened.add(id);
      expanded.set(run.id, opened); renderLog(run);
      const index = run.steps.findIndex(step => step.id === id);
      const summaryButton = $(`step-${index}`)?.querySelector('button');
      if (focus) summaryButton?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
      summaryButton?.focus({preventScroll: true});
      break;
    }
    case 'decline': respond(id, 'decline', button.closest('form')); break;
  }
});
$('task-form').addEventListener('submit', event => { event.preventDefault(); startTask(); });
$('task-prompt').addEventListener('input', renderActive);
$('task-prompt').addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); startTask(); }
});
$('clarifications').addEventListener('submit', event => {
  event.preventDefault(); respond(event.target.dataset.requestId, 'accept', event.target);
});
$('clarifications').addEventListener('input', event => {
  const form = event.target.closest('form');
  const request = activeRun()?.clarifications.find(item => item.id === form?.dataset.requestId);
  if (!request || request.state !== 'pending') return;
  const entry = Object.entries(request.schema.properties)[Number(event.target.dataset.fieldIndex)];
  if (!entry) return;
  const [key, prop] = entry;
  request.values[key] = prop.type === 'boolean' && !prop.enum ? event.target.checked
    : prop.enum ? (event.target.value ? JSON.parse(event.target.value) : '') : event.target.value;
  clearTimeout(persistTimer); persistTimer = setTimeout(persist, 300);
});
window.addEventListener('pagehide', () => { if (activeRun()) persist(); });
setInterval(() => { const run = selectedRun(); if (run && isActive(run)) $('run-duration').textContent = duration(elapsed(run)); }, 1000);

for (const node of document.querySelectorAll('[data-icon]')) node.innerHTML = icon(node.dataset.icon, 18);
$('status-legend').innerHTML = ['pending', 'running', 'waiting_input', 'done', 'error', 'skipped', 'cancelled'].map(statusPill).join('');
showStorageWarning(loaded.warning);
render();
connect().catch(() => setConnection('offline'));
