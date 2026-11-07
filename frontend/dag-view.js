import {dagHTML, edgePaths, layoutDag, NODE_HEIGHT, NODE_WIDTH} from './dag.js';

// Zoom around a fixed screen point, so the node under the cursor stays put.
export function zoomAt(camera, factor, point) {
  const scale = Math.min(3, Math.max(.02, camera.scale * factor));
  const ratio = scale / camera.scale;
  return {scale, x: point.x - (point.x - camera.x) * ratio, y: point.y - (point.y - camera.y) * ratio};
}

export class DagView {
  constructor(container, toolbar) {
    this.container = container;
    this.toolbar = toolbar;
    this.views = new Map();
    this.steps = [];
    this.view = null;
    this.drag = null;
    this.suppressClick = false;
    this.width = 1; this.height = 1;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    toolbar.addEventListener('click', event => {
      const action = event.target.closest('[data-dag-action]')?.dataset.dagAction;
      if (!action || !this.view) return;
      if (action === 'in') this.zoom(1.25);
      if (action === 'out') this.zoom(1 / 1.25);
      if (action === 'actual') this.zoom(1 / this.view.camera.scale);
      if (action === 'fit') this.fit();
      if (action === 'arrange') {
        this.view.layout = layoutDag(this.steps);
        this.draw(); this.fit();
      }
    });
    container.addEventListener('wheel', event => {
      if (!this.view || this.drag) return;
      event.preventDefault();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? this.height : 1);
      const rect = container.getBoundingClientRect();
      this.zoom(Math.exp(-delta * .0015), {x: event.clientX - rect.left, y: event.clientY - rect.top});
    }, {passive: false});
    container.addEventListener('pointerdown', event => this.pointerDown(event));
    container.addEventListener('pointermove', event => this.pointerMove(event));
    container.addEventListener('pointerup', event => this.pointerEnd(event));
    container.addEventListener('pointercancel', event => this.pointerEnd(event));
    container.addEventListener('lostpointercapture', () => { this.drag = null; container.classList.remove('is-dragging'); });
    container.addEventListener('click', event => {
      if (!this.suppressClick) return;
      this.suppressClick = false;
      event.preventDefault(); event.stopImmediatePropagation();
    }, true);
    container.addEventListener('pointerover', event => this.highlight(event.target.closest('.dag-node')?.dataset.id));
    container.addEventListener('pointerout', event => {
      const node = event.relatedTarget instanceof Element ? event.relatedTarget.closest('.dag-node') : null;
      this.highlight(node?.dataset.id);
    });
    container.addEventListener('focusin', event => this.highlight(event.target.closest('.dag-node')?.dataset.id));
    container.addEventListener('focusout', () => this.highlight(null));
    container.addEventListener('keydown', event => this.keyDown(event));
  }

  render(steps, key) {
    if (!steps.length) { this.clear(); return; }
    const signature = JSON.stringify(steps.map(step => [step.id, step.depends_on || []]));
    const previous = this.view;
    try {
      let view = this.views.get(key);
      if (!view || view.signature !== signature) {
        view = {signature, layout: layoutDag(steps), camera: {x: 0, y: 0, scale: 1}, autoFit: true};
        this.views.set(key, view);
      }
      if (view !== previous) this.pointerEnd();
      this.view = view; this.steps = steps;
      this.container.classList.add('has-graph');
      this.toolbar.querySelectorAll('button').forEach(button => button.disabled = false);
      this.draw(); this.resize();
    } catch {
      this.clear();
      this.container.innerHTML = '<div class="dag-empty">Не удалось отобразить зависимости. Сведения о шагах доступны в журнале.</div>';
    }
  }

  clear() {
    this.pointerEnd(); this.view = null; this.steps = []; this.svg = null;
    this.container.classList.remove('has-graph');
    this.toolbar.querySelectorAll('button').forEach(button => button.disabled = true);
  }

  draw() {
    this.container.innerHTML = dagHTML(this.steps, this.view.layout);
    this.svg = this.container.querySelector('svg.dag');
    this.applyCamera();
  }

  resize() {
    if (!this.view || !this.svg) return;
    const width = this.container.clientWidth, height = this.container.clientHeight;
    if (!width || !height) return;
    const camera = this.view.camera;
    if (!this.view.autoFit && this.view.viewport) {
      camera.x += (width - this.view.viewport.width) / 2;
      camera.y += (height - this.view.viewport.height) / 2;
    }
    this.width = width; this.height = height;
    this.view.viewport = {width, height};
    if (this.view.autoFit) this.fit(); else this.applyCamera();
  }

  fit() {
    if (!this.view) return;
    const points = [...this.view.layout.positions.values()].flatMap(p => [p, {x: p.x + NODE_WIDTH, y: p.y + NODE_HEIGHT}]);
    for (const route of this.view.layout.waypoints.values()) points.push(...route);
    const minX = Math.min(...points.map(p => p.x)), maxX = Math.max(...points.map(p => p.x));
    const minY = Math.min(...points.map(p => p.y)), maxY = Math.max(...points.map(p => p.y));
    const scale = Math.max(.02, Math.min(1, (this.width - 48) / (maxX - minX + 1), (this.height - 48) / (maxY - minY + 1)));
    this.view.camera = {scale, x: this.width / 2 - (minX + maxX) / 2 * scale, y: this.height / 2 - (minY + maxY) / 2 * scale};
    this.view.autoFit = true;
    this.applyCamera();
  }

  zoom(factor, point = {x: this.width / 2, y: this.height / 2}) {
    if (!this.view) return;
    this.view.camera = zoomAt(this.view.camera, factor, point);
    this.view.autoFit = false; this.applyCamera();
  }

  applyCamera() {
    if (!this.view || !this.svg) return;
    const {x, y, scale} = this.view.camera;
    this.svg.setAttribute('viewBox', `0 0 ${this.width} ${this.height}`);
    this.svg.setAttribute('width', '100%'); this.svg.setAttribute('height', '100%');
    this.svg.querySelector('.dag-scene')?.setAttribute('transform', `translate(${x} ${y}) scale(${scale})`);
    this.toolbar.querySelector('[data-dag-action="actual"]').textContent = `${Math.round(scale * 100)}%`;
  }

  pointerDown(event) {
    if (!this.view || !event.isPrimary || event.button !== 0) return;
    const nodeId = event.target.closest('.dag-node')?.dataset.id;
    const node = nodeId ? this.view.layout.positions.get(nodeId) : null;
    this.drag = {
      pointer: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false,
      nodeId, origin: node ? {...node} : {...this.view.camera},
    };
    // Capture only after moving: a plain node click must still open the log.
  }

  pointerMove(event) {
    const drag = this.drag;
    if (!drag || drag.pointer !== event.pointerId || !this.view) return;
    const dx = event.clientX - drag.startX, dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 5) return;
    if (!drag.moved) {
      drag.moved = true;
      this.container.setPointerCapture(event.pointerId);
      this.container.classList.add('is-dragging');
      this.view.autoFit = false;
    }
    event.preventDefault();
    if (drag.nodeId) {
      const scale = this.view.camera.scale;
      this.view.layout.positions.set(drag.nodeId, {x: drag.origin.x + dx / scale, y: drag.origin.y + dy / scale});
      this.updatePositions(); this.highlight(drag.nodeId);
    } else {
      this.view.camera.x = drag.origin.x + dx;
      this.view.camera.y = drag.origin.y + dy;
      this.applyCamera();
    }
  }

  pointerEnd(event) {
    if (!this.drag || (event && event.pointerId !== this.drag.pointer)) return;
    const {pointer, moved} = this.drag;
    this.drag = null;
    if (moved) {
      this.suppressClick = true;
      clearTimeout(this.clickTimer);
      this.clickTimer = setTimeout(() => { this.suppressClick = false; }, 300);
    }
    if (this.container.hasPointerCapture(pointer)) this.container.releasePointerCapture(pointer);
    this.container.classList.remove('is-dragging');
  }

  updatePositions() {
    for (const element of this.svg.querySelectorAll('[data-node-id]')) {
      const position = this.view.layout.positions.get(element.dataset.nodeId);
      element.setAttribute('x', position.x); element.setAttribute('y', position.y);
    }
    const paths = new Map(edgePaths(this.steps, this.view.layout).map(edge => [`${edge.from}->${edge.to}`, edge.d]));
    for (const link of this.svg.querySelectorAll('.dag-link')) {
      const d = paths.get(`${link.dataset.from}->${link.dataset.to}`);
      for (const path of link.querySelectorAll('path')) path.setAttribute('d', d);
    }
  }

  highlight(id) {
    if (!this.svg) return;
    this.svg.classList.toggle('has-highlight', Boolean(id));
    for (const link of this.svg.querySelectorAll('.dag-link')) {
      link.classList.toggle('is-related', Boolean(id) && (link.dataset.from === id || link.dataset.to === id));
    }
  }

  keyDown(event) {
    if (!this.view) return;
    const directions = {ArrowLeft: [-40, 0], ArrowRight: [40, 0], ArrowUp: [0, -40], ArrowDown: [0, 40]};
    const direction = directions[event.key];
    const nodeId = event.target.closest('.dag-node')?.dataset.id;
    if (direction && nodeId && event.altKey) {
      event.preventDefault();
      const node = this.view.layout.positions.get(nodeId);
      node.x += direction[0] / 4; node.y += direction[1] / 4;
      this.view.autoFit = false; this.updatePositions();
    } else if (event.target === this.container) {
      if (direction) {
        event.preventDefault(); this.view.autoFit = false;
        this.view.camera.x += direction[0]; this.view.camera.y += direction[1]; this.applyCamera();
      } else if (['+', '=', '-'].includes(event.key)) {
        event.preventDefault(); this.zoom(event.key === '-' ? .8 : 1.25);
      } else if (event.key === 'Home') { event.preventDefault(); this.fit(); }
    }
  }
}
