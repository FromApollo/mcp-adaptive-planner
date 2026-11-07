// Plain data shared by rendering and WebSocket handlers. No DOM or network here.
export const HISTORY_KEY = 'planner-runs-v2';
const ACTIVE_STATUSES = new Set(['planning', 'running', 'waiting_input', 'cancelling']);

export function isActive(run) {
  return Boolean(run && ACTIVE_STATUSES.has(run.status));
}

export function newRun(prompt, id, now = Date.now()) {
  return {
    id, serverRunId: null, prompt, status: 'planning', createdAt: now,
    endedAt: null, steps: [], clarifications: [], error: null, errorCode: null,
  };
}

export function updateStep(run, update) {
  const index = run.steps.findIndex(step => step.id === update.id);
  if (index >= 0) Object.assign(run.steps[index], update);
  else run.steps.push({...update});
}

export function closeRun(run, status, code = null, message = null, now = Date.now()) {
  run.status = status;
  run.endedAt = now;
  run.errorCode = code;
  run.error = message;
  for (const step of run.steps) {
    if (['pending', 'running', 'waiting_input'].includes(step.status)) {
      Object.assign(step, {status: 'cancelled', error_code: code || 'cancelled', error: message});
    }
  }
  for (const request of run.clarifications) {
    if (['pending', 'submitting'].includes(request.state)) request.state = 'closed';
  }
}

export function applyEvent(run, event, now = Date.now()) {
  switch (event.type) {
    case 'run_started': run.serverRunId = event.run_id; break;
    case 'plan':
      run.plan = event.plan;
      run.steps = event.plan.steps.map(step => ({...step, status: 'pending', attempts: 0}));
      run.status = 'running';
      break;
    case 'step_update':
      updateStep(run, event);
      break;
    case 'elicitation':
      run.clarifications.push({
        id: event.request_id, message: event.message, schema: event.requested_schema,
        state: 'pending', values: Object.create(null), createdAt: now,
      });
      break;
    case 'input_error': {
      const request = run.clarifications.find(item => item.id === event.request_id);
      if (request) Object.assign(request, {state: 'pending', error: event.message, errorCode: event.code});
      break;
    }
    case 'input_accepted': {
      const request = run.clarifications.find(item => item.id === event.request_id);
      if (request) Object.assign(request, {state: 'answered', answeredAt: now, error: null});
      break;
    }
    case 'input_closed': {
      const request = run.clarifications.find(item => item.id === event.request_id);
      if (request && request.state === 'pending') request.state = 'closed';
      break;
    }
    case 'done':
      for (const step of event.execution || []) updateStep(run, step);
      closeRun(run, event.success ? 'done' : 'error', event.success ? null : 'execution_failed', null, now);
      break;
    case 'cancelled': closeRun(run, 'cancelled', 'cancelled', event.message, now); break;
    case 'error': closeRun(run, 'error', event.code || 'internal_error', event.message, now); break;
  }
  if (isActive(run)) {
    const waiting = run.clarifications.some(request => ['pending', 'submitting'].includes(request.state));
    if (run.status !== 'cancelling') run.status = waiting ? 'waiting_input' : run.plan ? 'running' : 'planning';
  }
}

export function progress(run) {
  return {done: run.steps.filter(step => step.status === 'done').length, total: run.steps.length};
}

export function loadHistory(storage, now = Date.now()) {
  try {
    const raw = storage.getItem(HISTORY_KEY);
    let runs;
    if (raw !== null) {
      const saved = JSON.parse(raw);
      if (saved.version !== 2 || !Array.isArray(saved.runs)) throw new Error('Invalid history');
      runs = saved.runs;
    } else {
      const legacy = JSON.parse(storage.getItem('planner-history') || '[]');
      if (!Array.isArray(legacy)) throw new Error('Invalid legacy history');
      runs = legacy.map((item, index) => ({
        ...newRun(String(item.task || ''), `legacy-${item.ts}-${index}`, Number(item.ts) || now),
        endedAt: Number(item.ts) || now, status: item.success ? 'done' : 'error', legacy: true,
        previousStepCount: item.steps, previousDurationMs: Number(item.elapsed) * 1000,
      }));
    }
    for (const run of runs) {
      if (!run || typeof run.id !== 'string' || typeof run.prompt !== 'string' ||
          !Number.isFinite(run.createdAt) || !Number.isFinite(new Date(run.createdAt).getTime()) ||
          !Array.isArray(run.steps) || !Array.isArray(run.clarifications) ||
          run.steps.some(step => !step || typeof step.id !== 'string') ||
          run.clarifications.some(q => !q || typeof q.id !== 'string' || !q.schema || !q.values)) throw new Error('Invalid run');
      if (isActive(run)) {
        closeRun(run, 'interrupted', 'connection_lost',
          'Соединение с прошлым запуском потеряно. Уже выполненные действия могли сохраниться.', now);
      }
    }
    return {runs, warning: null};
  } catch {
    return {runs: [], warning: 'Не удалось прочитать историю браузера. Новые запуски доступны в текущем окне.'};
  }
}

export function saveHistory(storage, runs) {
  try {
    storage.setItem(HISTORY_KEY, JSON.stringify({version: 2, runs}));
    return null;
  } catch {
    return 'Не удалось сохранить историю в браузере. Освободите место, удалив ненужные записи. Текущий запуск доступен до закрытия страницы.';
  }
}

export function removeRun(runs, id, activeId) {
  if (id === activeId) return null;
  const index = runs.findIndex(run => run.id === id);
  if (index < 0) return null;
  return {run: runs.splice(index, 1)[0], index};
}

export function restoreRun(runs, deleted) {
  if (deleted && !runs.some(run => run.id === deleted.run.id)) {
    runs.splice(Math.min(deleted.index, runs.length), 0, deleted.run);
  }
}
