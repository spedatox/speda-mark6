// SPDX-License-Identifier: AGPL-3.0-or-later
import controlsCss from '../../../../../speda-go/app/src/main/assets/artifact-controls.css?raw'
import controlsScript from '../../../../../speda-go/app/src/main/assets/artifact-controls.js?raw'
import diagramCss from '../../../../../speda-go/app/src/main/assets/markvi-diagram.css?raw'

const diagramTokens: Record<string, string> = {
  PANEL: '--hb-petrol', TEXT: '--hb-text', MUTED: '--hb-text-dim',
  ACCENT: '--hb-cyan-bright', GOOD: '--hb-green', BAD: '--hb-red',
}
const diagramStyles = diagramCss.replace(/__([A-Z]+)__/g, (_, name) => `var(${diagramTokens[name]})`)

export const WIDGET_THEME_DEFAULTS: Record<string, string> = {
  '--hb-text': '#dbe6ec', '--hb-text-dim': '#98adb8', '--hb-text-faint': '#7e929d',
  '--hb-cyan': '#36abca', '--hb-cyan-bright': '#74cfe3', '--hb-accent-rgb': '54,171,202',
  '--bg-primary': '#0d1419', '--bg-sidebar': '#151f26', '--border': 'rgba(219,230,236,.14)',
  '--hb-petrol': '#16252d', '--hb-green': '#4fa377', '--hb-red': '#c84a3a',
}

export function buildWidgetDocument(raw: string, theme: Record<string, string> = {}, vector = false): string {
  const vars = Object.entries({ ...WIDGET_THEME_DEFAULTS, ...theme })
    .map(([key, value]) => `${key}:${value.replace(/[<>]/g, '')}`).join(';')
  const head = `<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{${vars};color-scheme:dark;--accent:var(--hb-cyan);--accent-hover:var(--hb-cyan-bright);--text-primary:var(--hb-text);--text-secondary:var(--hb-text-dim);--text-muted:var(--hb-text-faint);--bg-secondary:var(--bg-sidebar);--bg-hover:rgba(255,255,255,.06);--bg-active:rgba(var(--hb-accent-rgb),.16);--border-focus:var(--accent)}
*,*::before,*::after{box-sizing:border-box}:where(html,body){margin:0;padding:0;background:transparent}
body{display:flow-root;font:14px/1.55 'Segoe UI',sans-serif;color:var(--hb-text);overflow-wrap:break-word}
body>svg{display:block;width:100%;height:auto}img,canvas{max-width:100%}a{color:var(--accent)}
html[data-hb-vector],html[data-hb-vector]>body{width:100%;height:100%;overflow:hidden}
${controlsCss}
${vector ? diagramStyles : ''}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
</style>`
  const observer = `<script>(function(){
${controlsScript}
const body=document.body, svg=${vector} ? body.querySelector(':scope > svg') : null;
if (svg) document.documentElement.dataset.hbVector='';
else body.dataset.hbWidget='';
const original=svg?.viewBox?.baseVal;
const declared=original?.width>0 && original.height>0
  ? [original.x,original.y,original.width,original.height]
  : [0,0,...['width','height'].map(name=>{
      const value=svg?.getAttribute(name);
      return value && /^\\d+(?:\\.\\d+)?(?:px)?$/.test(value) ? parseFloat(value) : 0;
    })];
let last='',queued=false,fit='';
const initialZoom=body.style.zoom;
if(!svg) addEventListener('message',event=>{
  const scale=event.data?.scale;
  if(event.source!==parent || event.data?.type!=='widget-scale' || !Number.isFinite(scale) || scale<.5 || scale>8) return;
  body.style.zoom=scale===1 ? initialZoom : String(scale);
  schedule();
});
function fitSvg(){
  const b=svg.getBBox();
  const [x,y,w,h]=declared;
  const pad=Math.max(2,Math.min(w||b.width,h||b.height)*.012);
  const left=w && b.x>=x ? x : Math.min(x,b.x)-pad;
  const top=h && b.y>=y ? y : Math.min(y,b.y)-pad;
  const right=w && b.x+b.width<=x+w ? x+w : Math.max(x+w,b.x+b.width)+pad;
  const bottom=h && b.y+b.height<=y+h ? y+h : Math.max(y+h,b.y+b.height)+pad;
  const width=right-left, height=bottom-top;
  const box=[left,top,width,height].join(' ');
  if(box!==fit && width>0 && height>0){
    fit=box;
    svg.setAttribute('viewBox',box);
    svg.setAttribute('preserveAspectRatio','xMidYMid meet');
    svg.style.setProperty('width','100%','important');
    svg.style.setProperty('height','100%','important');
  }
  return width>0 && height>0 ? width/height : 1.6;
}
function report(){
  queued=false;
  const ratio=svg ? fitSvg() : 0;
  const zoom=parseFloat(getComputedStyle(body).zoom)||1;
  const height=svg ? 0 : Math.ceil(Math.max(body.scrollHeight*zoom,body.getBoundingClientRect().height));
  const controls=svg ? 0 : artifactControls(body);
  const key=[height,ratio,controls].join('/');
  if(key!==last){last=key;parent.postMessage({type:'widget-height',height,ratio,controls},'*')}
}
// Invisible iframes may suspend rAF; measuring must also work before reveal.
function schedule(){if(!queued){queued=true;setTimeout(report,0)}}
addEventListener('load',schedule);addEventListener('input',schedule);addEventListener('error',function(e){parent.postMessage({type:'widget-error',message:e.message||'Widget resource failed to load'},'*')},true);
if(!svg){addEventListener('resize',schedule);new ResizeObserver(schedule).observe(body)}
new MutationObserver(schedule).observe(body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['hidden','disabled','class','aria-disabled']});
document.fonts.ready.then(schedule);schedule();
})();</script>`
  let doc = raw.trim()
  if (!/^<!doctype\s|^<html[\s>]/i.test(doc)) return `<!doctype html><html><head>${head}</head><body>${doc}${observer}</body></html>`
  if (/<head\b[^>]*>/i.test(doc)) doc = doc.replace(/<head\b[^>]*>/i, `$&${head}`)
  else if (/<html\b[^>]*>/i.test(doc)) doc = doc.replace(/<html\b[^>]*>/i, `$&<head>${head}</head>`)
  else doc = doc.replace(/<!doctype[^>]*>/i, `$&<head>${head}</head>`)
  return /<\/body>/i.test(doc) ? doc.replace(/<\/body>/i, `${observer}</body>`) : doc + observer
}
