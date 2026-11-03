// SPDX-License-Identifier: AGPL-3.0-or-later
export const WIDGET_THEME_DEFAULTS: Record<string, string> = {
  '--hb-text': '#dbe6ec', '--hb-text-dim': '#98adb8', '--hb-text-faint': '#7e929d',
  '--hb-cyan': '#36abca', '--hb-cyan-bright': '#74cfe3', '--hb-accent-rgb': '54,171,202',
}

export function buildWidgetDocument(raw: string, theme: Record<string, string> = {}): string {
  const vars = Object.entries({ ...WIDGET_THEME_DEFAULTS, ...theme })
    .map(([key, value]) => `${key}:${value.replace(/[<>]/g, '')}`).join(';')
  const head = `<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{${vars};color-scheme:dark;--accent:var(--hb-cyan);--accent-hover:var(--hb-cyan-bright);--text-primary:var(--hb-text);--text-secondary:var(--hb-text-dim);--text-muted:var(--hb-text-faint);--bg-primary:#0d1419;--bg-secondary:#151f26;--bg-sidebar:#151f26;--bg-hover:rgba(255,255,255,.06);--bg-active:rgba(var(--hb-accent-rgb),.16);--border:rgba(219,230,236,.14);--border-focus:var(--accent)}
*,*::before,*::after{box-sizing:border-box}html,body{margin:0;padding:0;background:transparent}body{display:flow-root;font:14px/1.55 'Segoe UI',sans-serif;color:var(--hb-text);overflow-wrap:break-word}body>svg{display:block;width:100%;height:auto}img,canvas{max-width:100%}input,select,textarea,button{font:inherit;color:inherit}input[type=range],input[type=checkbox]{accent-color:var(--accent)}input[type=range]{display:block;width:100%;margin:8px 0;cursor:pointer}input[type=number],select,textarea{background:var(--bg-secondary);border:1px solid var(--border);border-radius:8px;padding:8px 10px;max-width:100%}button{background:var(--bg-secondary);border:1px solid var(--border);border-radius:8px;padding:8px 14px;cursor:pointer}button:hover{border-color:var(--accent)}:focus-visible{outline:2px solid var(--accent);outline-offset:3px}label{color:var(--text-secondary)}output{font-variant-numeric:tabular-nums;color:var(--text-primary)}a{color:var(--accent)}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
</style>`
  const observer = `<script>(function(){
let last=0,queued=false;
function report(){queued=false;const b=document.body;if(!b)return;const h=Math.ceil(Math.max(b.scrollHeight,b.getBoundingClientRect().height));if(h!==last){last=h;parent.postMessage({type:'widget-height',height:h},'*')}}
// Invisible iframes may suspend rAF; measuring must also work before reveal.
function schedule(){if(!queued){queued=true;setTimeout(report,0)}}
addEventListener('load',schedule);addEventListener('resize',schedule);addEventListener('input',schedule);addEventListener('error',function(e){parent.postMessage({type:'widget-error',message:e.message||'Widget resource failed to load'},'*')},true);
new ResizeObserver(schedule).observe(document.body);new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true,characterData:true});document.fonts.ready.then(schedule);schedule();
})();</script>`
  let doc = raw.trim()
  if (!/^<!doctype\s|^<html[\s>]/i.test(doc)) return `<!doctype html><html><head>${head}</head><body>${doc}${observer}</body></html>`
  if (/<head\b[^>]*>/i.test(doc)) doc = doc.replace(/<head\b[^>]*>/i, `$&${head}`)
  else if (/<html\b[^>]*>/i.test(doc)) doc = doc.replace(/<html\b[^>]*>/i, `$&<head>${head}</head>`)
  else doc = doc.replace(/<!doctype[^>]*>/i, `$&<head>${head}</head>`)
  return /<\/body>/i.test(doc) ? doc.replace(/<\/body>/i, `${observer}</body>`) : doc + observer
}
