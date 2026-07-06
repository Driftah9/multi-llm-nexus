"""Nexus Ops Board — FastAPI app.

A local admin console for a Nexus deployment. Built on Nexus's own FastAPI/uvicorn stack
(no second web framework), with the HTML/CSS/JS inlined so it drops into any install with
no template engine or static assets.

Tabs are agnostic — each reads the operator's own Nexus config or a live scan:
  * Providers — config/providers.yaml, by type/tier/role; keys present/absent only.
  * Diag      — the self-diagnostic report (core.diag_report), Rendered/Raw + download/copy.

Both reuse core.diag_report's collectors, so the console and `nexus doctor` can never
disagree. The console is local only — it never transmits anything.
"""
from __future__ import annotations

import json
import urllib.request

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from src.core import diag_report

app = FastAPI(title="Nexus Ops Board", docs_url=None, redoc_url=None)

# Known env var names that may appear in .env but not yet in providers.yaml.
# NEXUS:PORTABLE — operators extend this list as they add providers.
_ENV_TO_PROVIDER_NAME: dict[str, str] = {
    "DEEPSEEK_API_KEY":    "DeepSeek",
    "OPENROUTER_API_KEY":  "OpenRouter",
    "HF_API_KEY":          "HuggingFace",
    "HUGGINGFACE_API_KEY": "HuggingFace",
    "COHERE_API_KEY":      "Cohere",
    "NVIDIA_NIM_API_KEY":  "NVIDIA NIM",
    "ANTHROPIC_API_KEY":   "Anthropic",
    "OPENAI_API_KEY":      "OpenAI",
    "GOOGLE_API_KEY":      "Google / Gemini",
    "TOGETHER_API_KEY":    "Together AI",
    "FIREWORKS_API_KEY":   "Fireworks",
    "REPLICATE_API_TOKEN": "Replicate",
    "ZAI_API_KEY":         "Z.ai (GLM)",
    "XAI_API_KEY":         "xAI (Grok)",
    "PERPLEXITY_API_KEY":  "Perplexity",
    "MISTRAL_API_KEY":     "Mistral",
    "GROQ_API_KEY":        "Groq",
    "CEREBRAS_API_KEY":    "Cerebras",
    "SAMBANOVA_API_KEY":   "SambaNova",
}


def _detect_uncatalogued(providers: list[dict]) -> dict:
    """Return API keys and local Ollama models present on the system but absent from providers.yaml."""
    import os

    # Collect all api_key env var names already declared in providers.yaml
    cfg = diag_report._load_providers_config()
    declared_keys: set[str] = set()
    declared_models: set[str] = set()
    for pc in (cfg.get("providers") or {}).values():
        if not isinstance(pc, dict):
            continue
        var, _ = diag_report._resolve_env_ref(pc.get("api_key", ""))
        if var:
            declared_keys.add(var)
        if pc.get("type") == "ollama" and pc.get("model"):
            declared_models.add(pc["model"])

    # API keys present in env but not in any providers.yaml entry
    detected_api: list[dict] = []
    for env_var, name in _ENV_TO_PROVIDER_NAME.items():
        if env_var in declared_keys:
            continue
        if os.environ.get(env_var, "").strip():
            detected_api.append({"name": name, "key_env": env_var})

    # Ollama models on disk but not in providers.yaml
    detected_local: list[dict] = []
    loaded_models: set[str] = set()
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=3) as r:
            for m in json.loads(r.read()).get("models", []):
                loaded_models.add(m.get("name", ""))
    except Exception:
        pass
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
            for m in json.loads(r.read()).get("models", []):
                name = m.get("name", "")
                if name not in declared_models:
                    size_bytes = m.get("size", 0)
                    detected_local.append({
                        "name": name,
                        "size": f"{size_bytes / 1e9:.1f} GB" if size_bytes else "?",
                        "loaded": name in loaded_models,
                    })
    except Exception:
        pass

    return {"detected_api": detected_api, "detected_local": detected_local}


@app.get("/api/providers")
def api_providers():
    providers = diag_report._collect_providers()
    detected = _detect_uncatalogued(providers)
    return JSONResponse({"providers": providers, **detected})


@app.get("/api/diag")
def api_diag(redact_paths: int = 1):
    md = diag_report.generate_markdown(redact_paths=bool(redact_paths))
    return JSONResponse({"markdown": md, "filename": diag_report.report_filename()})


@app.get("/", response_class=HTMLResponse)
def index():
    return _PAGE


def serve(host: str = "127.0.0.1", port: int = 8137) -> None:
    import uvicorn
    print(f"Nexus Ops Board → http://{host}:{port}  (local console, nothing is transmitted)")
    uvicorn.run(app, host=host, port=port, log_level="warning")


_PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nexus Ops Board</title>
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;
--muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#a78bfa;}
.si{display:inline-block;font-weight:700;font-size:13px;width:18px;height:18px;
line-height:18px;text-align:center;border-radius:3px;vertical-align:middle;margin-right:4px;}
.si.green{color:var(--green);background:rgba(63,185,80,.15);}
.si.blue{color:var(--accent);background:rgba(88,166,255,.15);}
.si.red{color:var(--red);background:rgba(248,81,73,.15);}
.si.yellow{color:var(--yellow);background:rgba(210,153,34,.15);}
.si.purple{color:var(--purple);background:rgba(167,139,250,.15);}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,
"Segoe UI",sans-serif;margin:0;padding:0}
header{background:var(--bg2);border-bottom:1px solid var(--border)}
.header-inner{display:flex;align-items:center;gap:10px;padding:14px 24px}
.brand-icon{color:var(--accent);font-size:20px}.brand-name{font-weight:600}
.brand-sub{color:var(--muted);font-size:12px;border-left:1px solid var(--border);
padding-left:10px;margin-left:4px}
nav.tabs{display:flex;padding:0 20px;gap:2px}
.tab{background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);
padding:10px 16px;font-size:14px;cursor:pointer}
.tab:hover{color:var(--text)}.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-right{margin-left:auto}
main{max-width:1100px;margin:0 auto;padding:20px}
.pane{display:none}.pane.active{display:block}
.loading{color:var(--muted);padding:20px}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}
th,td{border:1px solid var(--border);padding:7px 11px;text-align:left}
th{background:var(--bg3);font-weight:600}
tr:nth-child(even) td{background:rgba(255,255,255,.02)}
.yes{color:var(--green)}.no{color:var(--red)}
.bar{display:flex;flex-direction:column;gap:10px;background:var(--bg2);
border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:14px}
.intro{color:var(--muted);font-size:13px}.intro strong{color:var(--text)}
.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
button.act{cursor:pointer;border:1px solid var(--border);border-radius:6px;font-size:13px;
padding:7px 13px;background:var(--bg3);color:var(--text)}
button.primary{background:var(--accent);color:#0d1117;border-color:var(--accent);font-weight:600}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:6px;overflow:hidden}
.seg button{border:none;border-radius:0;background:var(--bg3);color:var(--muted);
padding:7px 12px;cursor:pointer;font-size:13px}
.seg button+button{border-left:1px solid var(--border)}
.seg button.active{background:var(--accent);color:#0d1117;font-weight:600}
.toggle{color:var(--muted);font-size:12px;margin-left:6px;display:inline-flex;
align-items:center;gap:5px;cursor:pointer}
.report{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
padding:18px 22px;max-height:72vh;overflow-y:auto}
pre.report{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;
line-height:1.55;white-space:pre-wrap;word-wrap:break-word;margin:0}
.rendered{font-size:14px;line-height:1.6}
.rendered h1{font-size:20px;margin:4px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--border)}
.rendered h2{font-size:16px;margin:22px 0 10px;color:var(--accent)}
.rendered p{margin:8px 0}.rendered em{color:var(--muted);font-style:italic}
.rendered ul{margin:8px 0;padding-left:22px}.rendered li{margin:3px 0}
.rendered code{background:var(--bg3);border:1px solid var(--border);border-radius:4px;
padding:1px 5px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}
.rendered hr{border:none;border-top:1px solid var(--border);margin:18px 0}
.rendered blockquote{margin:12px 0;padding:10px 14px;border-left:3px solid var(--accent);
background:var(--bg3);border-radius:0 6px 6px 0;color:var(--muted);font-size:13px}
.rendered blockquote strong{color:var(--text)}
</style></head><body>
<header>
  <div class="header-inner">
    <span class="brand-icon">⬡</span><span class="brand-name">Nexus Ops Board</span>
    <span class="brand-sub">local console · nothing is transmitted</span>
  </div>
  <nav class="tabs">
    <button class="tab active" data-tab="providers">⬡ Providers</button>
    <button class="tab tab-right" data-tab="diag">⚕ Diag</button>
  </nav>
</header>
<main>
  <div id="pane-providers" class="pane active"><div class="loading">Loading providers…</div></div>
  <div id="pane-diag" class="pane"><div class="loading">Generating diagnostic report…</div></div>
</main>
<script>
const loaded=new Set();
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  b.classList.add('active');const t=b.dataset.tab;
  document.getElementById('pane-'+t).classList.add('active');
  if(!loaded.has(t)){load(t);loaded.add(t);}
}));
function load(t){if(t==='providers')loadProviders();else if(t==='diag')regenDiag();}

// ── Providers ──
function si(cls,sym,title){return `<span class="si ${cls}" title="${esc(title)}">${sym}</span>`;}
function keyIndicator(p){
  const isLocal=p.cost_class==='local'||p.type==='ollama';
  if(isLocal)return si('purple','✓','Local — no key required');
  if(p.key_present===true)return si('green','✓','API key configured');
  if(p.key_present===false)return si('red','✗','Key missing: '+p.name);
  return si('blue','✓','Subscription — always available');
}
function loadProviders(){
  fetch('/api/providers').then(r=>r.json()).then(d=>{
    const ps=d.providers||[];
    const detApi=d.detected_api||[];
    const detLocal=d.detected_local||[];
    const pane=document.getElementById('pane-providers');
    if(!ps.length&&!detApi.length&&!detLocal.length){pane.innerHTML='<div class="loading">No providers configured — '
      +'copy <code>config/providers.yaml.example</code> to <code>config/providers.yaml</code>.</div>';return;}
    let h='<table><thead><tr><th>Status</th><th>Provider</th><th>Type</th><th>Tier</th><th>Role</th>'
      +'<th>Cost class</th></tr></thead><tbody>';
    ps.forEach(p=>{h+=`<tr><td>${keyIndicator(p)}</td><td>${esc(p.name)}</td><td>${esc(p.type)}</td>`
      +`<td>${esc(p.tier)}</td><td>${esc(p.role)}</td><td>${esc(p.cost_class)}</td></tr>`;});
    h+='</tbody></table>';
    if(detApi.length||detLocal.length){
      h+='<h3 style="margin:22px 0 8px;color:var(--yellow)">⚠ Detected — Not Registered</h3>'
        +'<p style="color:var(--muted);font-size:13px;margin:0 0 10px">Present on system but absent from providers.yaml</p>'
        +'<table><thead><tr><th>Status</th><th>Provider / Model</th><th>Source</th><th>Info</th></tr></thead><tbody>';
      detApi.forEach(d=>{h+=`<tr><td>${si('red','✗','Key present but not wired into providers.yaml')}</td>`
        +`<td><strong>${esc(d.name)}</strong></td><td class="mono" style="color:var(--muted)">${esc(d.key_env)}</td>`
        +`<td style="color:var(--muted)">API key present</td></tr>`;});
      detLocal.forEach(d=>{const ind=d.loaded?si('purple','✓','Loaded in memory'):si('purple','✗','On disk, not loaded');
        h+=`<tr><td>${ind}</td><td><strong>${esc(d.name)}</strong></td>`
        +`<td style="color:var(--muted)">ollama local</td><td style="color:var(--muted)">${d.loaded?'loaded':'unloaded'} · ${esc(d.size)}</td></tr>`;});
      h+='</tbody></table>';
    }
    pane.innerHTML=h;
  }).catch(e=>{document.getElementById('pane-providers').innerHTML=
    `<div class="loading">Failed: ${e.message}</div>`;});
}

// ── Diag ──
let md='',filename='nexus-diag.md',view='rendered';
function mdToHtml(src){
  const inline=s=>esc(s).replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>')
    .replace(/(^|[^A-Za-z0-9_])_([^_]+)_(?=[^A-Za-z0-9_]|$)/g,'$1<em>$2</em>')
    .replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  const L=String(src).split('\\n');const out=[];let i=0;
  const tbl=l=>/^\\s*\\|.*\\|\\s*$/.test(l);
  while(i<L.length){const line=L[i];let m;
    if((m=line.match(/^(#{1,6})\\s+(.*)$/))){out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);i++;continue;}
    if(/^---+\\s*$/.test(line)){out.push('<hr>');i++;continue;}
    if(/^>\\s?/.test(line)){const b=[];while(i<L.length&&/^>\\s?/.test(L[i])){b.push(inline(L[i].replace(/^>\\s?/,'')));i++;}out.push(`<blockquote>${b.join('<br>')}</blockquote>`);continue;}
    if(tbl(line)&&i+1<L.length&&/^\\s*\\|[\\s:|-]+\\|\\s*$/.test(L[i+1])){
      const cells=l=>l.trim().replace(/^\\||\\|$/g,'').split('|').map(c=>c.trim());
      const head=cells(line);i+=2;const rows=[];
      while(i<L.length&&tbl(L[i])){rows.push(cells(L[i]));i++;}
      let t='<table><thead><tr>'+head.map(h=>`<th>${inline(h)}</th>`).join('')+'</tr></thead><tbody>';
      t+=rows.map(r=>'<tr>'+r.map(c=>`<td>${inline(c)}</td>`).join('')+'</tr>').join('');
      out.push(t+'</tbody></table>');continue;}
    if(/^\\s*-\\s+/.test(line)){const it=[];while(i<L.length&&/^\\s*-\\s+/.test(L[i])){it.push(`<li>${inline(L[i].replace(/^\\s*-\\s+/,''))}</li>`);i++;}out.push(`<ul>${it.join('')}</ul>`);continue;}
    if(/^\\s*$/.test(line)){i++;continue;}
    const b=[];while(i<L.length&&!/^\\s*$/.test(L[i])&&!/^#{1,6}\\s/.test(L[i])&&!/^>\\s?/.test(L[i])&&!/^\\s*-\\s+/.test(L[i])&&!/^---+\\s*$/.test(L[i])&&!tbl(L[i])){b.push(inline(L[i]));i++;}
    out.push(`<p>${b.join(' ')}</p>`);}
  return out.join('\\n');
}
function diagBody(){return view==='raw'?`<pre class="report">${esc(md)}</pre>`
  :`<div class="report rendered">${mdToHtml(md)}</div>`;}
function diagShell(){return `
  <div class="bar">
    <div class="intro">Self-generated report — <strong>nothing is sent anywhere</strong>.
      Download it, copy it, and drop it into a GitHub issue or email yourself.</div>
    <div class="actions">
      <button class="act primary" onclick="downloadDiag()">⬇ Download .md</button>
      <button class="act" onclick="copyDiag(this)">⧉ Copy</button>
      <button class="act" onclick="regenDiag()">↻ Regenerate</button>
      <span class="seg">
        <button id="v-rendered" class="${view==='rendered'?'active':''}" onclick="setView('rendered')">Rendered</button>
        <button id="v-raw" class="${view==='raw'?'active':''}" onclick="setView('raw')">Raw</button>
      </span>
      <label class="toggle"><input type="checkbox" id="redact" checked onchange="regenDiag()">
        redact paths / LAN IPs</label>
    </div>
  </div>
  <div id="diag-content">${diagBody()}</div>`;}
function setView(v){view=v;const c=document.getElementById('diag-content');
  if(c)c.innerHTML=diagBody();
  const r=document.getElementById('v-rendered'),w=document.getElementById('v-raw');
  if(r)r.classList.toggle('active',v==='rendered');if(w)w.classList.toggle('active',v==='raw');}
function downloadDiag(){const blob=new Blob([md],{type:'text/markdown'});
  const u=URL.createObjectURL(blob);const a=document.createElement('a');
  a.href=u;a.download=filename;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(u);}
function copyDiag(btn){navigator.clipboard.writeText(md).then(()=>{const t=btn.textContent;
  btn.textContent='✓ Copied';setTimeout(()=>btn.textContent=t,1500);});}
function regenDiag(){const cb=document.getElementById('redact');const keep=cb?cb.checked:true;
  const pane=document.getElementById('pane-diag');
  if(!md)pane.innerHTML='<div class="loading">Generating diagnostic report…</div>';
  fetch(`/api/diag?redact_paths=${keep?1:0}`).then(r=>r.json()).then(d=>{
    md=d.markdown||'';filename=d.filename||'nexus-diag.md';
    pane.innerHTML=diagShell();const c=document.getElementById('redact');if(c)c.checked=keep;})
    .catch(e=>{pane.innerHTML=`<div class="loading">Failed: ${e.message}</div>`;});}

loadProviders();loaded.add('providers');
</script></body></html>"""


if __name__ == "__main__":
    serve()
