/* Local interaction study for the static maintenance draft; no service or model calls. */
(async () => {
  const stage = document.querySelector('.branch-stage');
  if (!stage) return;
  const {nodes, labels} = JSON.parse(document.getElementById('branch-data').textContent);
  const viewport = stage.querySelector('.branch-viewport');
  const canvas = stage.querySelector('.branch-canvas');
  const drawing = stage.querySelector('.branch-drawing');
  const nodeLayer = stage.querySelector('.branch-nodes');
  const popover = stage.querySelector('.branch-popover');
  const byId = new Map(nodes.map(n => [n.id, {...n, children: [], depth: 0, group: -1}]));
  const root = [...byId.values()].find(n => n.parent === null);
  for (const node of byId.values()) if (node.parent !== null) byId.get(node.parent).children.push(node.id);
  function classify(node, depth, group) {
    node.depth = depth; node.group = group;
    node.children.forEach((id, i) => classify(byId.get(id), depth + 1, depth === 0 ? i % 4 : group));
  }
  classify(root, 0, -1);
  const counts = [...byId.values()].map(n => n.count);
  const low = Math.min(...counts), high = Math.max(...counts);
  const thickness = n => high === low ? 1.25 : 1.25 + (n.count - low) / (high - low) * 7.75;
  const collapsed = new Set(nodes.filter(n => n.collapsed).map(n => n.id));
  const ns = 'http://www.w3.org/2000/svg';
  const controls = new Map(), strokes = new Map(), links = new Map();
  let active = null, current, frame, timer;
  function svg(tag, cls, node) {
    const e = document.createElementNS(ns, tag);
    e.setAttribute('class', `${cls} branch-group-${node.group < 0 ? 'root' : node.group + 1}`);
    e.setAttribute('stroke-width', thickness(node));e.dataset.node=node.id;
    drawing.append(e); return e;
  }
  for (const node of byId.values()) {
    if (node.parent !== null) links.set(node.id, svg('path', 'tree-link', node));
    strokes.set(node.id, svg('line', 'tree-branch', node));
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'branch-node';
    button.dataset.node = node.id; button.setAttribute('role', 'treeitem');
    button.setAttribute('aria-level', node.depth + 1);
    const siblings = node.parent === null ? [root.id] : byId.get(node.parent).children;
    button.setAttribute('aria-setsize', siblings.length); button.setAttribute('aria-posinset', siblings.indexOf(node.id) + 1);
    const name = document.createElement('span'); name.className = 'branch-name'; name.textContent = node.name;
    const count = document.createElement('span'); count.className = 'branch-count num'; count.textContent = node.count;
    const more = document.createElement('span'); more.className = 'branch-more num';
    button.append(name, count, more); nodeLayer.append(button); controls.set(node.id, button);
    button.addEventListener('pointerenter', () => activate(node.id));
    button.addEventListener('pointerleave', closeSoon);
    button.addEventListener('focus', () => activate(node.id));
    button.addEventListener('click', () => toggle(node));
    button.addEventListener('keydown', event => {
      if (event.key === 'Tab' && !event.shiftKey && node.parent !== null && active === node.id) {
        event.preventDefault(); popover.querySelector('button').focus();
      }
      // Native buttons map Enter/Space to click, including fold toggles.
    });
  }
  function layout() {
    let leaf = 0;
    const result = new Map();
    // Measure the actual label and count, including a collapsed child-count suffix.
    const lengths = new Map();
    for (const node of byId.values()) {
      const button = controls.get(node.id);
      button.querySelector('.branch-more').textContent = collapsed.has(node.id) && node.children.length ? `＋${node.children.length}` : '';
      lengths.set(node.id, Math.ceil(button.getBoundingClientRect().width + 24));
    }
    function visit(node) {
      const children = collapsed.has(node.id) ? [] : node.children;
      const ys = children.map(id => visit(byId.get(id)));
      const y = ys.length ? (ys[0] + ys[ys.length - 1]) / 2 : 48 + leaf++ * 44;
      // Forks need more room than a one-child continuation. Large fans grow locally.
      const gap = ys.length > 1 ? Math.max(144, (ys[ys.length - 1] - ys[0]) * .45) : 72;
      result.set(node.id, {x:0, y, length:lengths.get(node.id), gap, opacity:1});
      return y;
    }
    visit(root);
    function position(node, x) {
      const point = result.get(node.id); point.x=x;
      if(!collapsed.has(node.id)) node.children.forEach(id => position(byId.get(id),x+point.length+point.gap));
    }
    position(root,16);
    // Hidden descendants converge on the parent's right-hand junction during a fold.
    function hide(node, ancestor) {
      const point = result.get(node.id) ?? {x:ancestor.x+ancestor.length,y:ancestor.y,length:0,gap:72,opacity:0};
      if (!result.has(node.id)) result.set(node.id, point);
      node.children.forEach(id => hide(byId.get(id), point));
    }
    hide(root, result.get(root.id));
    result.height = Math.max(160, 48 + Math.max(0, leaf - 1) * 44 + 48);
    result.width = 16 + Math.max(...[...result.values()].filter(p=>p.opacity===1).map(p=>p.x+p.length));
    return result;
  }
  function paint(points, complete = true) {
    canvas.style.height = `${points.height}px`; canvas.style.minWidth = `${points.width}px`;
    drawing.setAttribute('width', Math.max(points.width, viewport.clientWidth)); drawing.setAttribute('height', points.height);
    for (const node of byId.values()) {
      const point = points.get(node.id), button = controls.get(node.id), branch = strokes.get(node.id);
      button.style.transform = `translate(${point.x}px,${point.y - 30}px)`;
      button.style.opacity = point.opacity;
      button.style.visibility = point.opacity === 0 && complete ? 'hidden' : 'visible';
      button.tabIndex = point.opacity === 1 ? 0 : -1;
      button.style.pointerEvents = point.opacity === 1 ? 'auto' : 'none';
      button.querySelector('.branch-more').textContent = collapsed.has(node.id) && node.children.length ? `＋${node.children.length}` : '';
      if (node.children.length) button.setAttribute('aria-expanded', !collapsed.has(node.id));
      button.setAttribute('aria-label', `${node.name}, ${node.count} ${labels.tasks}${node.children.length ? ', ' + (collapsed.has(node.id) ? labels.expand : labels.collapse) : ''}`);
      for (const [key,value] of Object.entries({x1:point.x,y1:point.y,x2:point.x+point.length,y2:point.y,opacity:point.opacity})) branch.setAttribute(key,value);
      if (node.parent !== null) {
        const parent = points.get(node.parent), from = parent.x + parent.length, gap = point.x - from;
        const path = links.get(node.id);
        path.setAttribute('d', `M${from} ${parent.y} C${from+gap*.38} ${parent.y+(point.y-parent.y)*.28},${point.x-gap*.35} ${point.y},${point.x} ${point.y}`);
        path.setAttribute('opacity',point.opacity);
      }
    }
    placePopover();
  }
  function toggle(node) {
    if (!node.children.length) return;
    if (collapsed.has(node.id)) collapsed.delete(node.id); else collapsed.add(node.id);
    const target = layout(), from = current;
    cancelAnimationFrame(frame);
    const duration = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 180;
    const start = performance.now();
    function step(now) {
      const progress = duration ? Math.min(1,(now-start)/duration) : 1;
      const eased = progress * progress * (3 - 2 * progress), points = new Map();
      for (const node of byId.values()) {
        const a=from.get(node.id), b=target.get(node.id);
        points.set(node.id,{x:a.x+(b.x-a.x)*eased,y:a.y+(b.y-a.y)*eased,length:a.length+(b.length-a.length)*eased,opacity:a.opacity+(b.opacity-a.opacity)*eased});
      }
      points.height=from.height+(target.height-from.height)*eased;points.width=from.width+(target.width-from.width)*eased;
      current=points;paint(points,progress===1);
      if(progress<1) frame=requestAnimationFrame(step);
    }
    frame=requestAnimationFrame(step);
  }
  function related(id) {
    const keep = new Set();
    let n=byId.get(id);
    while(n){keep.add(n.id);n=byId.get(n.parent)}
    function down(n){keep.add(n.id);n.children.forEach(child=>down(byId.get(child)))}
    down(byId.get(id));return keep;
  }
  function activate(id) {
    clearTimeout(timer); active=id;
    const keep=related(id), node=byId.get(id);
    for(const n of byId.values()) {
      strokes.get(n.id).dataset.dim = !keep.has(n.id);
      if(links.has(n.id)) links.get(n.id).dataset.dim = !keep.has(n.id);
    }
    popover.hidden=false;popover.dataset.node=id;
    popover.querySelector('h3').textContent=node.name;
    popover.querySelector('.pop-count').textContent=`${node.count} ${labels.tasks}`;
    popover.querySelector('.pop-definition').textContent=node.definition;
    const merged=popover.querySelector('.pop-merged');merged.hidden=!node.mergedFrom;merged.textContent=node.mergedFrom?`${labels.merged} ${node.mergedFrom}`:'';
    popover.querySelector('.actions').hidden=node.parent===null;
    placePopover();
  }
  function placePopover() {
    if(!active || popover.hidden || !current) return;
    const point=current.get(active), width=popover.offsetWidth, height=popover.offsetHeight;
    popover.style.left=`${Math.max(8,Math.min(point.x+point.length+16-viewport.scrollLeft,stage.clientWidth-width-8))}px`;
    popover.style.top=`${Math.max(8,Math.min(point.y+18,current.height-height-8))}px`;
  }
  function clear() {
    active=null;popover.hidden=true;
    for(const e of [...strokes.values(),...links.values()]) e.dataset.dim=false;
  }
  function closeSoon() {
    clearTimeout(timer);timer=setTimeout(()=>{
      if(popover.matches(':hover')) return;
      const focused=document.activeElement.closest('.branch-node');
      if(focused) activate(focused.dataset.node); else if(!popover.contains(document.activeElement)) clear();
    },140);
  }
  popover.addEventListener('pointerenter',()=>clearTimeout(timer));popover.addEventListener('pointerleave',closeSoon);
  stage.addEventListener('focusout',()=>setTimeout(()=>{if(!stage.contains(document.activeElement))clear()},0));
  popover.addEventListener('keydown',event=>{
    const buttons=[...popover.querySelectorAll('button')];
    if(event.key!=='Tab')return;
    if(event.shiftKey && document.activeElement===buttons[0]){event.preventDefault();controls.get(active).focus()}
    else if(!event.shiftKey && document.activeElement===buttons.at(-1)){
      const visible=[...byId.values()].filter(n=>current.get(n.id).opacity===1);
      const next=visible[visible.findIndex(n=>n.id===active)+1];
      if(next){event.preventDefault();controls.get(next.id).focus()}
    }
  });
  viewport.addEventListener('scroll',placePopover);window.addEventListener('resize',()=>{paint(current);placePopover()});
  await document.fonts.ready;
  current=layout();paint(current);clear();stage.dataset.ready='true';
})();
