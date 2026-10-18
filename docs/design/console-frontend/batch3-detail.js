// Interaction for this static review only; all data stays in the HTML.
const strip = document.querySelector('.strip-scroll');
const svg = document.querySelector('.strip-svg');
const segments = [...document.querySelectorAll('[data-step]')];
const selectedStarts = new Map(segments.map(segment => [segment.dataset.step, Number(segment.dataset.start)]));
const labels = [...svg.querySelectorAll('text')].map(element => ({ element, x: Number(element.getAttribute('x')), text: element.textContent }));
const shapes = [...svg.querySelectorAll('rect, path, circle, line')];
let zoom = 1;
const maximum = 60 / (1064 / 40);
const padding = () => Number.parseFloat(getComputedStyle(strip).paddingLeft);
const minimum = () => (strip.clientWidth - padding() * 2) / 1120;

function selectStep(segment) {
  segments.forEach(item => item.setAttribute('aria-pressed', String(item === segment)));
  document.querySelectorAll('.step-view').forEach(view => { view.hidden = view.id !== segment.dataset.step; });
  document.querySelector('.detail-content').scrollTop = 0;
}

function draw() {
  svg.style.width = `${1120 * zoom}px`;
  svg.setAttribute('viewBox', `0 0 ${1120 * zoom} 112`);
  shapes.forEach(element => element.setAttribute('transform', `scale(${zoom} 1)`));
  let lastAxisRight = -Infinity;
  labels.forEach(({ element, x, text }) => {
    element.setAttribute('x', String(x * zoom));
    element.textContent = text;
    element.style.visibility = 'visible';
    if (element.getAttribute('y') === '18') {
      const width = element.getComputedTextLength();
      const left = element.getAttribute('text-anchor') === 'end' ? x * zoom - width : x * zoom;
      // Reserve the current-time label before thinning intermediate ticks.
      if (x !== 1096 && (left < lastAxisRight + 12 || left + width > 1096 * zoom - 90)) element.style.visibility = 'hidden';
      else lastAxisRight = left + width;
    }
    const segment = element.closest('[data-step]');
    const bar = segment?.querySelector('.past-bar, .router-bar');
    if (bar && element.getAttribute('y') === '75') {
      const available = Number(bar.getAttribute('width')) * zoom - 12;
      if (element.getComputedTextLength() > available) element.textContent = text.match(/\d+ 分$/)[0];
      if (element.getComputedTextLength() > available) element.style.visibility = 'hidden';
    }
    if (text === '等待验收' && element.getComputedTextLength() > 133 * zoom - 8) element.style.visibility = 'hidden';
  });
  document.querySelector('[data-zoom=out]').setAttribute('aria-disabled', String(zoom <= minimum()));
  document.querySelector('[data-zoom=fit]').setAttribute('aria-disabled', String(zoom <= minimum()));
  document.querySelector('[data-zoom=in]').setAttribute('aria-disabled', String(zoom >= maximum));
}

segments.forEach(segment => {
  segment.addEventListener('click', () => selectStep(segment));
  segment.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectStep(segment); }
  });
});
document.querySelectorAll('[data-zoom]').forEach(button => button.addEventListener('click', () => {
  const selected = svg.querySelector('[aria-pressed=true]');
  const at = selectedStarts.get(selected.dataset.step);
  const screenX = padding() + at * zoom - strip.scrollLeft;
  zoom = button.dataset.zoom === 'fit' ? minimum() : Math.max(minimum(), Math.min(maximum, zoom * (button.dataset.zoom === 'in' ? 2 : .5)));
  draw();
  strip.scrollLeft = button.dataset.zoom === 'fit' ? 0 : padding() + at * zoom - screenX;
}));

draw();
// Focus the opening interval once; selecting and scrolling never follows it.
strip.scrollLeft = 816 - strip.clientWidth / 2;
if (document.documentElement.dataset.theme === 'dark') document.querySelectorAll('a[href$=".html"]').forEach(link => { link.href += '?theme=dark'; });
document.documentElement.dataset.ready = 'true';
