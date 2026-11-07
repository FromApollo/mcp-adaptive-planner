import {esc, icon, STATUS, stepTitle} from './ui.js';

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 88;
const COLUMN_GAP = 116;
const ROW_GAP = 132;
const PADDING = 28;
const edgeKey = (from, to) => `${from}->${to}`;

// Long edges receive invisible intermediate vertices. They get their own lanes
// rather than passing through a real node in an intermediate dependency level.
export function layoutDag(steps) {
  if (new Set(steps.map(step => step.id)).size !== steps.length) throw new Error('Duplicate node');
  const levels = new Map();
  const remaining = [...steps];
  while (remaining.length) {
    const ready = remaining.filter(step => (step.depends_on || []).every(id => levels.has(id)));
    if (!ready.length) throw new Error('Invalid dependency graph');
    for (const step of ready) {
      levels.set(step.id, Math.max(-1, ...(step.depends_on || []).map(id => levels.get(id))) + 1);
      remaining.splice(remaining.indexOf(step), 1);
    }
  }
  const columns = Array.from({length: Math.max(-1, ...levels.values()) + 1}, () => []);
  const previous = new Map(), next = new Map(), routes = new Map();
  for (const step of steps) columns[levels.get(step.id)].push(step.id);
  function link(from, to) {
    if (!next.has(from)) next.set(from, []);
    if (!previous.has(to)) previous.set(to, []);
    next.get(from).push(to); previous.get(to).push(from);
  }
  let edgeIndex = 0;
  for (const step of steps) {
    for (const from of step.depends_on || []) {
      let cursor = from;
      const vertices = [];
      for (let rank = levels.get(from) + 1; rank < levels.get(step.id); rank++) {
        const vertex = `@${edgeIndex}:${rank}`;
        columns[rank].push(vertex); vertices.push(vertex);
        link(cursor, vertex); cursor = vertex;
      }
      link(cursor, step.id);
      routes.set(edgeKey(from, step.id), vertices);
      edgeIndex++;
    }
  }
  // Barycentric sweeps align related branches and reduce avoidable crossings.
  function reorder(rank, neighbors, adjacentRank) {
    const order = new Map(columns[adjacentRank].map((id, index) => [id, index]));
    const scores = new Map(columns[rank].map((id, index) => {
      const connected = (neighbors.get(id) || []).filter(item => order.has(item));
      return [id, connected.length ? connected.reduce((sum, item) => sum + order.get(item), 0) / connected.length : index];
    }));
    columns[rank].sort((a, b) => scores.get(a) - scores.get(b));
  }
  for (let sweep = 0; sweep < 4; sweep++) {
    for (let rank = 1; rank < columns.length; rank++) reorder(rank, previous, rank - 1);
    for (let rank = columns.length - 2; rank >= 0; rank--) reorder(rank, next, rank + 1);
  }
  const height = Math.max(240, ...columns.map(column => column.length * ROW_GAP + 2 * PADDING));
  const allPositions = new Map();
  columns.forEach((column, rank) => column.forEach((id, index) => allPositions.set(id, {
    x: PADDING + rank * (NODE_WIDTH + COLUMN_GAP),
    y: PADDING + (height - 2 * PADDING) * (index + .5) / column.length - NODE_HEIGHT / 2,
  })));
  const positions = new Map(steps.map(step => [step.id, allPositions.get(step.id)]));
  const waypoints = new Map([...routes].map(([key, vertices]) => [key, vertices.map(id => {
    const p = allPositions.get(id);
    return {x: p.x + NODE_WIDTH / 2, y: p.y + NODE_HEIGHT / 2};
  })]));
  return {width: Math.max(276, columns.length * (NODE_WIDTH + COLUMN_GAP) - COLUMN_GAP + 2 * PADDING), height, positions, waypoints};
}

// Give each incoming/outgoing connection its own port on the edge of the card.
export function edgePaths(steps, layout) {
  const edges = steps.flatMap(step => (step.depends_on || []).map(from => ({from, to: step.id})));
  const incoming = new Map(), outgoing = new Map();
  for (const edge of edges) {
    if (!incoming.has(edge.to)) incoming.set(edge.to, []);
    if (!outgoing.has(edge.from)) outgoing.set(edge.from, []);
    incoming.get(edge.to).push(edge); outgoing.get(edge.from).push(edge);
  }
  for (const group of outgoing.values()) group.sort((a, b) => layout.positions.get(a.to).y - layout.positions.get(b.to).y);
  for (const group of incoming.values()) group.sort((a, b) => layout.positions.get(a.from).y - layout.positions.get(b.from).y);
  return edges.map(edge => {
    const from = layout.positions.get(edge.from), to = layout.positions.get(edge.to);
    const outputs = outgoing.get(edge.from), inputs = incoming.get(edge.to);
    const points = [
      {x: from.x + NODE_WIDTH, y: from.y + NODE_HEIGHT * (outputs.indexOf(edge) + 1) / (outputs.length + 1)},
      ...(layout.waypoints.get(edgeKey(edge.from, edge.to)) || []),
      {x: to.x, y: to.y + NODE_HEIGHT * (inputs.indexOf(edge) + 1) / (inputs.length + 1)},
    ];
    let d = `M${points[0].x} ${points[0].y}`;
    for (let index = 1; index < points.length; index++) {
      const a = points[index - 1], b = points[index];
      const bend = Math.max(32, Math.abs(b.x - a.x) * .48);
      d += ` C${a.x + bend} ${a.y}, ${b.x - bend} ${b.y}, ${b.x} ${b.y}`;
    }
    return {...edge, d, points};
  });
}

export function dagHTML(steps, layout = null) {
  if (!steps.length) return '';
  try {
    layout ||= layoutDag(steps);
    const edges = edgePaths(steps, layout).map(edge => `<g class="dag-link" data-from="${esc(edge.from)}" data-to="${esc(edge.to)}"><title>Шаг ${esc(edge.from)} → шаг ${esc(edge.to)}</title><path class="dag-edge-halo" d="${edge.d}"/><path class="dag-edge" d="${edge.d}" marker-end="url(#dag-arrow)"/></g>`).join('');
    const nodes = steps.map(step => {
      const {x, y} = layout.positions.get(step.id);
      const status = STATUS[step.status] ? step.status : 'pending';
      const [label, name] = STATUS[status];
      return `<foreignObject data-node-id="${esc(step.id)}" x="${x}" y="${y}" width="${NODE_WIDTH}" height="${NODE_HEIGHT}">
        <button xmlns="http://www.w3.org/1999/xhtml" type="button" class="dag-node dag-node--${status}" data-action="focus-step" data-id="${esc(step.id)}" title="${esc(stepTitle(step))}. Перетащите для перемещения; нажмите, чтобы открыть журнал.">
          <span class="dag-node-icon">${icon(name, 16)}</span><span class="dag-node-copy">
            <small>Шаг ${esc(step.id)} · ${label}</small><strong>${esc(stepTitle(step))}</strong>
          </span>
        </button></foreignObject>`;
    }).join('');
    return `<svg class="dag" viewBox="0 0 ${layout.width} ${layout.height}" width="${layout.width}" height="${layout.height}" role="group" aria-label="Граф зависимостей шагов">
      <defs><marker id="dag-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" class="dag-arrow-head"/></marker></defs>
      <g class="dag-scene">${edges}${nodes}</g></svg>`;
  } catch {
    return '<div class="dag-empty">Не удалось отобразить зависимости. Сведения о шагах доступны в журнале.</div>';
  }
}
