import test from 'node:test';
import assert from 'node:assert/strict';
import {applyEvent, closeRun, HISTORY_KEY, isActive, loadHistory, newRun, progress, removeRun, restoreRun, saveHistory} from '../frontend/state.js';
import {dagHTML, edgePaths, layoutDag, NODE_HEIGHT, NODE_WIDTH} from '../frontend/dag.js';
import {zoomAt} from '../frontend/dag-view.js';
import {errorText, esc, fieldLabel, stepTitle} from '../frontend/ui.js';

function memoryStorage(initial = {}) {
  const items = new Map(Object.entries(initial));
  return {getItem: key => items.get(key) ?? null, setItem: (key, value) => items.set(key, value)};
}
const step = (id, depends_on = []) => ({id, server: 'sysinfo', tool: 'get_memory', description: 'Получить сведения', depends_on});

test('parallel dependencies create a real DAG, not a sequential list', () => {
  const steps = [step('cpu'), step('ram'), step('save', ['cpu', 'ram'])];
  const {positions} = layoutDag(steps);
  assert.equal(positions.get('cpu').x, positions.get('ram').x);
  assert.notEqual(positions.get('cpu').y, positions.get('ram').y);
  assert.ok(positions.get('save').x > positions.get('cpu').x);
  const svg = dagHTML(steps);
  assert.equal((svg.match(/class="dag-edge"/g) || []).length, 2);
  assert.ok(svg.includes('data-from="cpu" data-to="save"'));
  assert.ok(svg.includes('data-from="ram" data-to="save"'));
});

test('DAG is independent of input order and rejects malformed dependencies', () => {
  const {positions} = layoutDag([step('save', ['read']), step('read')]);
  assert.ok(positions.get('save').x > positions.get('read').x);
  assert.throws(() => layoutDag([step('1', ['2']), step('2', ['1'])]));
  assert.throws(() => layoutDag([step('1', ['unknown'])]));
  assert.ok(dagHTML([step('1', ['unknown'])]).includes('журнале'));
});

test('layout reorders crossed branches instead of following input order', () => {
  const layout = layoutDag([step('a'), step('b'), step('c', ['b']), step('d', ['a'])]);
  const order = id => layout.positions.get(id).y;
  assert.equal(Math.sign(order('a') - order('b')), Math.sign(order('d') - order('c')));
});

test('long dependencies have lanes outside intermediate nodes', () => {
  const steps = [step('a'), step('b', ['a']), step('c', ['b']), step('d', ['c', 'a'])];
  const layout = layoutDag(steps);
  const route = edgePaths(steps, layout).find(edge => edge.from === 'a' && edge.to === 'd');
  assert.equal(route.points.length, 4); // endpoints and two intermediate lanes
  for (const point of route.points.slice(1, -1)) {
    for (const position of layout.positions.values()) {
      assert.equal(point.x > position.x && point.x < position.x + NODE_WIDTH &&
                   point.y > position.y && point.y < position.y + NODE_HEIGHT, false);
    }
  }
});

test('connections use distinct ports and follow a manually moved node', () => {
  const steps = [step('a'), step('b'), step('c', ['a', 'b'])];
  const layout = layoutDag(steps);
  const before = edgePaths(steps, layout);
  assert.notEqual(before[0].points.at(-1).y, before[1].points.at(-1).y);
  layout.positions.get('a').x += 75;
  layout.positions.get('a').y += 60;
  const after = edgePaths(steps, layout);
  assert.equal(after[0].points[0].x - before[0].points[0].x, 75);
  assert.equal(after[0].points[0].y - before[0].points[0].y, 60);
  assert.notEqual(after[0].d, before[0].d);
});

test('zoom preserves the world point under the cursor and clamps extremes', () => {
  const camera = {x: -150, y: 80, scale: .5}, point = {x: 420, y: 180};
  const next = zoomAt(camera, 1.5, point);
  assert.equal((point.x - camera.x) / camera.scale, (point.x - next.x) / next.scale);
  assert.equal((point.y - camera.y) / camera.scale, (point.y - next.y) / next.scale);
  assert.equal(zoomAt(camera, 10000, point).scale, 3);
  assert.equal(zoomAt(camera, .000001, point).scale, .02);
});

test('server results, failures and skipped steps survive persistence and reopening', () => {
  const run = newRun('Прочитать и скопировать', 'local-id', 1000);
  applyEvent(run, {type: 'run_started', run_id: 'server-id'});
  applyEvent(run, {type: 'plan', plan: {steps: [step('1'), step('2', ['1'])]}});
  applyEvent(run, {type: 'step_update', id: '1', status: 'running', attempts: 1});
  applyEvent(run, {type: 'done', success: false, execution: [
    {id: '1', status: 'error', error_code: 'not_found', error: 'File missing', duration_ms: 10},
    {id: '2', status: 'skipped', error_code: 'dependency_failed', blocked_by: ['1'], attempts: 0},
  ]}, 2000);
  assert.equal(isActive(run), false);
  const storage = memoryStorage();
  assert.equal(saveHistory(storage, [run]), null);
  const [restored] = loadHistory(storage).runs;
  assert.equal(restored.id, 'local-id');
  assert.equal(restored.serverRunId, 'server-id');
  assert.equal(restored.steps[0].error_code, 'not_found');
  assert.deepEqual(restored.steps[1].depends_on, ['1']);
  assert.deepEqual(restored.steps[1].blocked_by, ['1']);
  assert.deepEqual(progress(restored), {done: 0, total: 2});
});

test('all successful outputs, including empty strings, are retained', () => {
  const run = newRun('copy', '1');
  applyEvent(run, {type: 'plan', plan: {steps: [step('a'), step('b', ['a'])]}});
  applyEvent(run, {type: 'done', success: true, execution: [
    {id: 'a', status: 'done', result: ''}, {id: 'b', status: 'done', result: 'Error: valid file content'},
  ]});
  assert.equal(run.steps[0].result, '');
  assert.equal(run.steps[1].result, 'Error: valid file content');
  assert.deepEqual(progress(run), {done: 2, total: 2});
});

test('elicitation can occur before planning and preserves accepted answers', () => {
  const run = newRun('Сохрани текст', '1');
  applyEvent(run, {type: 'elicitation', request_id: 'q', message: 'Уточните', requested_schema: {
    type: 'object', properties: {path: {type: 'string'}}, required: ['path'],
  }});
  assert.equal(run.status, 'waiting_input');
  run.clarifications[0].values.path = 'data/report.txt';
  run.clarifications[0].state = 'submitting';
  applyEvent(run, {type: 'input_accepted', request_id: 'q'}, 2000);
  applyEvent(run, {type: 'input_closed', request_id: 'q'});
  assert.equal(run.status, 'planning');
  assert.equal(run.clarifications[0].state, 'answered');
  assert.equal(run.clarifications[0].values.path, 'data/report.txt');
  assert.equal(run.clarifications[0].answeredAt, 2000);
});

test('parallel questions are correlated; rejecting one answer keeps the form usable', () => {
  const run = newRun('task', '1');
  const event = {type: 'elicitation', message: 'Q', requested_schema: {properties: {}}};
  applyEvent(run, {...event, request_id: 'one'});
  applyEvent(run, {...event, request_id: 'two'});
  run.clarifications[0].state = 'submitting';
  applyEvent(run, {type: 'input_error', request_id: 'one', code: 'invalid_input', message: 'Invalid'});
  assert.equal(run.clarifications[0].state, 'pending');
  applyEvent(run, {type: 'input_accepted', request_id: 'two'});
  assert.equal(run.status, 'waiting_input');
  assert.equal(run.clarifications[0].state, 'pending');
  assert.equal(run.clarifications[1].state, 'answered');
});

test('reloading an unfinished run never resumes execution or leaves live forms', () => {
  const run = newRun('task', '1', 1000);
  applyEvent(run, {type: 'plan', plan: {steps: [step('a'), step('b')]}});
  applyEvent(run, {type: 'step_update', id: 'a', status: 'done', result: 'retained'});
  applyEvent(run, {type: 'elicitation', request_id: 'q', requested_schema: {properties: {}}});
  const storage = memoryStorage();
  saveHistory(storage, [run]);
  const [restored] = loadHistory(storage, 4000).runs;
  assert.equal(restored.status, 'interrupted');
  assert.equal(isActive(restored), false);
  assert.equal(restored.steps[0].status, 'done');
  assert.equal(restored.steps[0].result, 'retained');
  assert.equal(restored.steps[1].status, 'cancelled');
  assert.equal(restored.clarifications[0].state, 'closed');
});

test('individual removal protects the active run, and undo restores order', () => {
  const runs = ['a', 'b', 'c'].map(id => newRun(id, id));
  assert.equal(removeRun(runs, 'a', 'a'), null);
  const deleted = removeRun(runs, 'b', 'a');
  assert.deepEqual(runs.map(run => run.id), ['a', 'c']);
  restoreRun(runs, deleted);
  restoreRun(runs, deleted);
  assert.deepEqual(runs.map(run => run.id), ['a', 'b', 'c']);
});

test('legacy history remains readable without fabricated plans or logs', () => {
  const storage = memoryStorage({'planner-history': JSON.stringify([
    {task: 'Old task', success: true, steps: 3, elapsed: 2.4, ts: 1000},
  ])});
  const [run] = loadHistory(storage).runs;
  assert.equal(run.legacy, true);
  assert.equal(run.status, 'done');
  assert.equal(run.previousDurationMs, 2400);
  assert.deepEqual(run.steps, []);
});

test('storage failures and corrupt history are visible instead of crashing', () => {
  assert.ok(loadHistory(memoryStorage({[HISTORY_KEY]: '{bad json'})).warning);
  const storage = {getItem() { throw new Error('Blocked'); }, setItem() { throw new Error('Quota'); }};
  assert.ok(loadHistory(storage).warning);
  assert.ok(saveHistory(storage, []).includes('Не удалось сохранить'));
});

test('untrusted task data is escaped in diagram markup', () => {
  const markup = dagHTML([{...step('1'), description: 'Текст <script>alert(1)</script> " onclick="evil'}]);
  assert.equal(markup.includes('<script>'), false);
  assert.ok(markup.includes('&lt;script&gt;'));
  assert.equal(esc('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');
});

test('Russian labels are available for real tools and server failures', () => {
  assert.equal(stepTitle({tool: 'filesystem.read_file', description: 'Read a file'}), 'Прочитать файл');
  assert.ok(errorText('rate_limited').includes('ограничил'));
  assert.ok(errorText('not_found').includes('не найдены'));
  assert.equal(fieldLabel('path', 'filesystem.write_file: path'), 'Путь к файлу или каталогу');
  assert.equal(fieldLabel('text', 'Какой текст сохранить?'), 'Какой текст сохранить?');
});

test('generic question IDs never replace the actual field question', () => {
  assert.equal(fieldLabel('q1', 'Какой текст сохранить?'), 'Какой текст сохранить?');
  assert.equal(fieldLabel('q2', 'Куда сохранить файл?'), 'Куда сохранить файл?');
  assert.equal(fieldLabel('q1', 'What text would you like to save?'), 'Какой текст сохранить?');
  assert.equal(fieldLabel('q2', 'Where should the file be saved?'), 'Куда сохранить файл?');
  assert.equal(fieldLabel('q1', 'q1', 'Введите текст для сохранения'), 'Введите текст для сохранения');
  assert.equal(fieldLabel('q3', 'Which categories should be included?'), 'Which categories should be included?');
});
