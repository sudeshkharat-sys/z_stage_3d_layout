"""VRML 1.0 + 2.0 / WRL → GLB / OBJ / STL Converter

Handles:
  - VRML 1.0 (Open Inventor) state machine: Coordinate3 + Material + IndexedFaceSet siblings
  - VRML 2.0 (VRML97): Transform/Shape/Appearance/Material/Coordinate hierarchy
  - DEF/USE references (named node reuse)
  - Same-color mesh merging + vertex deduplication for smaller output
  - Server-Sent Events (SSE) real progress stream
  - Color mode: actual VRML colors or uniform light-gray

Usage:
    pip install flask trimesh[easy] numpy
    python app.py
Open http://localhost:5555
"""

import io
import json
import logging
import re
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np
from flask import Flask, Response, jsonify, render_template_string, request, send_file

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024

# ── Job store ──────────────────────────────────────────────────────────────────
_jobs = {}
_jobs_lock = threading.Lock()


def _job_set(jid, **kw):
    with _jobs_lock:
        _jobs[jid].update(kw)


def _job_get(jid):
    with _jobs_lock:
        return dict(_jobs.get(jid, {}))


# ── HTML ────────────────────────────────────────────────────────────────────────
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>VRML / WRL Converter</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0;
    min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 2rem; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 2.5rem;
    width: 100%; max-width: 580px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
  h1 { font-size: 1.6rem; color: #7dd3fc; margin-bottom: .25rem; }
  .sub { color: #94a3b8; font-size: .9rem; margin-bottom: 2rem; }
  label { display: block; font-size: .85rem; color: #94a3b8; margin-bottom: .4rem; }
  .drop-zone { border: 2px dashed #334155; border-radius: 12px; padding: 2.5rem;
    text-align: center; cursor: pointer; transition: border-color .2s,background .2s; margin-bottom: 1.5rem; }
  .drop-zone:hover,.drop-zone.dragover { border-color: #7dd3fc; background: #0f172a; }
  .drop-zone input[type=file] { display: none; }
  .drop-zone .icon { font-size: 2.5rem; margin-bottom: .5rem; }
  .drop-zone .hint { color: #64748b; font-size: .85rem; margin-top: .4rem; }
  .drop-zone .chosen { color: #7dd3fc; font-weight: 600; margin-top: .6rem; font-size: .95rem; }
  .row { display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  .field { flex: 1; min-width: 120px; }
  select,input[type=number] { width: 100%; background: #0f172a; border: 1px solid #334155;
    border-radius: 8px; color: #e2e8f0; padding: .55rem .75rem; font-size: .9rem; }
  select:focus,input[type=number]:focus { outline: none; border-color: #7dd3fc; }
  .color-toggle { display: flex; gap: .5rem; }
  .color-btn { flex: 1; padding: .55rem .5rem; border-radius: 8px; border: 1px solid #334155;
    background: #0f172a; color: #94a3b8; font-size: .82rem; cursor: pointer; text-align: center;
    transition: all .15s; }
  .color-btn.active { border-color: #7dd3fc; background: #1e3a5f; color: #7dd3fc; font-weight: 600; }
  button { width: 100%; background: #0284c7; color: #fff; border: none; border-radius: 10px;
    padding: .85rem; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background .2s; }
  button:hover { background: #0369a1; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  .progress-wrap { margin-top: 1.5rem; display: none; }
  .progress-bar { background: #1e3a5f; border-radius: 8px; height: 10px; overflow: hidden; margin-bottom: .5rem; }
  .progress-fill { height: 100%; background: linear-gradient(90deg,#0284c7,#7dd3fc);
    border-radius: 8px; width: 0%; transition: width .4s ease; }
  .progress-label { font-size: .85rem; color: #94a3b8; text-align: center; }
  .result { margin-top: 1.5rem; padding: 1rem 1.25rem; border-radius: 10px; font-size: .9rem; display: none; }
  .result.ok  { background: #052e16; border: 1px solid #16a34a; color: #86efac; }
  .result.err { background: #2d0a0a; border: 1px solid #dc2626; color: #fca5a5; }
  .stats { margin-top: .5rem; font-size: .82rem; color: #64748b; line-height: 1.6; }
</style>
</head>
<body>
<div class="card">
  <h1>&#127922; VRML / WRL Converter</h1>
  <p class="sub">VRML 1.0 &amp; 2.0 &mdash; runs locally on CPU</p>
  <label>1. Choose your WRL / VRML file</label>
  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <input type="file" id="fileInput" accept=".wrl,.vrml" onchange="onFileChosen(this)"/>
    <div class="icon">&#128196;</div>
    <div>Click to browse or drag &amp; drop</div>
    <div class="hint">Supports .wrl &amp; .vrml &mdash; VRML 1.0 &amp; 2.0</div>
    <div class="chosen" id="chosenName"></div>
  </div>
  <div class="row">
    <div class="field">
      <label>2. Output format</label>
      <select id="outFmt">
        <option value="glb">GLB</option>
        <option value="obj">OBJ</option>
        <option value="stl">STL</option>
      </select>
    </div>
    <div class="field">
      <label>3. Max faces <span style="color:#64748b;font-size:.78rem">(use 5M+ for large files)</span></label>
      <input type="number" id="maxFaces" value="5000000" min="5000" max="100000000" step="500000"/>
    </div>
  </div>
  <div style="margin-bottom:1.5rem">
    <label>4. Color mode</label>
    <div class="color-toggle">
      <div class="color-btn active" id="btnActual" onclick="setColor('actual')">&#127912; Actual VRML colors</div>
      <div class="color-btn" id="btnGray" onclick="setColor('gray')">&#9632; Uniform light gray</div>
    </div>
  </div>
  <button id="convertBtn" onclick="convert()" disabled>Convert</button>
  <div class="progress-wrap" id="progressWrap">
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-label" id="progressLabel">Starting…</div>
  </div>
  <div class="result" id="resultBox"></div>
</div>
<script>
let chosenFile=null, colorMode='actual', evtSrc=null;
const dz=document.getElementById('dropZone');
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('dragover');});
dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));
dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('dragover');const f=e.dataTransfer.files[0];if(f)setFile(f);});
function onFileChosen(i){if(i.files[0])setFile(i.files[0]);}
function setFile(f){chosenFile=f;document.getElementById('chosenName').textContent=f.name+'  ('+fmt(f.size)+')';document.getElementById('convertBtn').disabled=false;}
function fmt(b){if(b>1e9)return(b/1e9).toFixed(1)+' GB';if(b>1e6)return(b/1e6).toFixed(1)+' MB';return(b/1e3).toFixed(0)+' KB';}
function setColor(m){colorMode=m;document.getElementById('btnActual').classList.toggle('active',m==='actual');document.getElementById('btnGray').classList.toggle('active',m==='gray');}
async function convert(){
  if(!chosenFile)return;
  if(evtSrc){evtSrc.close();evtSrc=null;}
  const btn=document.getElementById('convertBtn'),pw=document.getElementById('progressWrap'),
    pf=document.getElementById('progressFill'),pl=document.getElementById('progressLabel'),rb=document.getElementById('resultBox');
  btn.disabled=true;rb.style.display='none';pw.style.display='block';pf.style.width='2%';pl.textContent='Uploading…';
  const form=new FormData();
  form.append('file',chosenFile);
  form.append('out_format',document.getElementById('outFmt').value);
  form.append('max_faces',document.getElementById('maxFaces').value);
  form.append('color_mode',colorMode);
  let startRes;
  try{
    const r=await fetch('/start',{method:'POST',body:form});
    startRes=await r.json();
    if(!r.ok){rb.className='result err';rb.style.display='block';rb.textContent='✗ '+(startRes.detail||'Upload failed.');btn.disabled=false;return;}
  }catch(e){rb.className='result err';rb.style.display='block';rb.textContent='✗ Network error during upload.';btn.disabled=false;return;}
  const jid=startRes.job_id;
  evtSrc=new EventSource('/progress/'+jid);
  evtSrc.onmessage=e=>{
    const d=JSON.parse(e.data);
    pf.style.width=d.pct+'%';
    pl.textContent=d.label+' ('+d.pct+'%)';
    if(d.status==='done'){
      evtSrc.close();evtSrc=null;
      pf.style.width='100%';pl.textContent='Done!';
      const a=document.createElement('a');
      a.href='/download/'+jid;
      a.download=chosenFile.name.replace(/\.[^.]+$/,'')+'.'+document.getElementById('outFmt').value;
      a.click();
      rb.className='result ok';rb.style.display='block';
      rb.innerHTML='✅ Converted!<div class="stats">Output size: '+fmt(d.size)+'<br/>Parts merged: '+d.parts+'<br/>Color mode: '+(colorMode==='actual'?'Actual VRML colors':'Uniform light gray')+'</div>';
      btn.disabled=false;
    }else if(d.status==='error'){
      evtSrc.close();evtSrc=null;
      rb.className='result err';rb.style.display='block';rb.textContent='✗ '+d.error;
      btn.disabled=false;
    }
  };
  evtSrc.onerror=()=>{
    if(evtSrc)evtSrc.close();evtSrc=null;
    rb.className='result err';rb.style.display='block';rb.textContent='✗ Connection lost.';
    btn.disabled=false;
  };
}
</script>
</body></html>
"""


# ── Compiled patterns ──────────────────────────────────────────────────────────
_FLT = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
_NUM_RE     = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_BRACE_RE   = re.compile(r'[{}]')
_BRACKET_RE = re.compile(r'[\[\]]')

_DIRECT_RE = re.compile(
    r'\b(MatrixTransform|Transform|Separator|Group|Switch'
    r'|TransformSeparator|Coordinate3|IndexedFaceSet|IndexedLineSet'
    r'|Material|MaterialBinding|Normal|NormalBinding|ShapeHints'
    r'|Info|Texture2|Texture2Transform|TextureCoordinate2'
    r'|PointLight|DirectionalLight|SpotLight|OrthographicCamera|PerspectiveCamera'
    r'|Cube|Sphere|Cone|Cylinder|AsciiText'
    r'|FontStyle|DrawStyle|LightModel|BaseColor|PackedColor'
    r'|EnvironmentMap|ShapeKit|WWWAnchor|WWWInline'
    r'|Shape|Appearance|Coordinate|Anchor|Billboard|Collision'
    r')\s*\{'
)

_DEF_RE            = re.compile(r'\bDEF\s+(\S+)\s+(\w+)\s*\{')
_USE_STANDALONE_RE = re.compile(r'(?<!\w)USE\s+(\S+)')
# Capture group 1 = the word USE so we can get its position without text.index()
_FIELD_USE_RE      = re.compile(
    r'\b(?:coord|appearance|geometry|material|color|normal|texCoord|children)\s+(USE)\s+(\S+)'
)

_PT_RE          = re.compile(r'\bpoint\s*\[')
_CI_RE          = re.compile(r'\bcoordIndex\s*\[')
_SEP            = r'[\s,]+'   # VRML allows commas as optional separators between values
_DC_RE          = re.compile(rf'\bdiffuseColor\s+\[?{_SEP}?({_FLT}){_SEP}({_FLT}){_SEP}({_FLT})')
_FLT16          = _SEP.join([rf'({_FLT})'] * 16)
_MTX_VALS_RE    = re.compile(r'\bmatrix\s+' + _FLT16)
_TR1_RE         = re.compile(rf'\btranslation\s+({_FLT}){_SEP}({_FLT}){_SEP}({_FLT})')
_SC1_RE         = re.compile(rf'\bscaleFactor\s+({_FLT})(?:{_SEP}({_FLT}){_SEP}({_FLT}))?')
_RO1_RE         = re.compile(rf'\brotation\s+({_FLT}){_SEP}({_FLT}){_SEP}({_FLT}){_SEP}({_FLT})')
_COORD_FIELD_RE = re.compile(r'\bcoord\s+(?:DEF\s+\S+\s+)?Coordinate\s*\{')
_COORD_USE_RE   = re.compile(r'\bcoord\s+USE\s+(\S+)')

_DEFAULT_COLOR = (230, 230, 230, 255)


# ── Index builders ─────────────────────────────────────────────────────────────
def _build_brace_index(text):
    """
    Build brace pair index as two sorted numpy arrays instead of a Python dict.
    For 9M pairs: dict ≈ 550 MB, numpy ≈ 140 MB — 4× less memory.
    Lookup: brace_close(pos) = binary search in opens array.
    """
    logger.info('Building brace index …')
    opens_list, closes_list, stack = [], [], []
    for m in _BRACE_RE.finditer(text):
        if m.group() == '{':
            stack.append(m.start())
        elif stack:
            o = stack.pop()
            opens_list.append(o)
            closes_list.append(m.start())
    opens  = np.array(opens_list,  dtype=np.int64)
    closes = np.array(closes_list, dtype=np.int64)
    order  = np.argsort(opens)
    opens  = opens[order]
    closes = closes[order]
    logger.info('Brace index: %d pairs', len(opens))
    return opens, closes


def _brace_close(brace_idx, pos):
    """Return close-brace position for the open-brace at pos, or None."""
    opens, closes = brace_idx
    i = np.searchsorted(opens, pos)
    if i < len(opens) and opens[i] == pos:
        return int(closes[i])
    return None



def _build_def_map(text, brace_idx):
    # Collect only the names that are actually USEd — avoids building 255K entries
    # when only 651 will ever be looked up.
    use_names = set()
    for m in _USE_STANDALONE_RE.finditer(text):
        use_names.add(m.group(1))
    for m in _FIELD_USE_RE.finditer(text):
        use_names.add(m.group(2))   # group 2 = name after USE
    logger.info('USE names referenced: %d', len(use_names))

    def_map = {}
    for m in _DEF_RE.finditer(text):
        name = m.group(1)
        if name not in use_names:
            continue   # skip — nothing ever USEs this DEF
        node_type = m.group(2)
        bo = text.find('{', m.start())
        if bo == -1:
            continue
        bc = _brace_close(brace_idx, bo)
        if bc is not None:
            def_map[name] = (node_type, bo + 1, bc)
    logger.info('DEF map: %d entries (skipped %d unused)', len(def_map), len(use_names) - len(def_map))
    return def_map


def _build_field_use_positions(text):
    positions = set()
    for m in _FIELD_USE_RE.finditer(text):
        positions.add(m.start(1))   # start of captured 'USE' group — no text.index() call
    return positions


# ── Helpers ────────────────────────────────────────────────────────────────────
def _bracket_pos(text, start, end, bracket_idx=None):
    """Find matching [ ] within [start, end). bracket_idx unused — kept for compat."""
    p = text.find('[', start)
    if p == -1 or p >= end:
        return None
    # Walk forward to find the matching ] (local scan, not full-file index)
    depth = 0
    for i in range(p, end):
        if text[i] == '[':
            depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0:
                return (p + 1, i)
    return None


def _axis_angle_to_mat4(x, y, z, angle):
    L = np.sqrt(x*x + y*y + z*z)
    if L < 1e-10:
        return np.eye(4)
    x, y, z = x/L, y/L, z/L
    c, s, t = np.cos(angle), np.sin(angle), 1 - np.cos(angle)
    m = np.eye(4)
    m[0,0]=t*x*x+c;   m[0,1]=t*x*y-s*z; m[0,2]=t*x*z+s*y
    m[1,0]=t*x*y+s*z; m[1,1]=t*y*y+c;   m[1,2]=t*y*z-s*x
    m[2,0]=t*x*z-s*y; m[2,1]=t*y*z+s*x; m[2,2]=t*z*z+c
    return m


def _parse_floats(text, pos_tuple):
    nums = _NUM_RE.findall(text[pos_tuple[0]:pos_tuple[1]])
    return np.array(nums, dtype=np.float64) if nums else None


def _parse_face_indices(text, start, end):
    raw = text[start:end].replace(',', ' ')
    arr = np.fromstring(raw, dtype=np.int32, sep=' ')
    return arr if len(arr) > 0 else np.array([], dtype=np.int32)


def _build_faces(indices):
    faces, fan = [], []
    for idx in indices:
        if idx < 0:
            if len(fan) >= 3:
                for j in range(1, len(fan) - 1):
                    faces.append((fan[0], fan[j], fan[j + 1]))
            fan = []
        else:
            fan.append(int(idx))
    if len(fan) >= 3:
        for j in range(1, len(fan) - 1):
            faces.append((fan[0], fan[j], fan[j + 1]))
    return np.array(faces, dtype=np.int32) if faces else np.empty((0, 3), dtype=np.int32)


def _extract_diffuse(text, cs, ce):
    # Search up to 800 chars — VRML 1.0 Material may have ambientColor/specularColor
    # before diffuseColor, and diffuseColor may use bracket form: diffuseColor [ r g b ]
    hdr = text[cs: min(cs + 800, ce)]
    dc = _DC_RE.search(hdr)
    if dc:
        return [int(float(dc.group(i)) * 255) for i in (1, 2, 3)] + [255]
    return None


# ── Pre-scanner: single pass, replaces O(N×depth) regex scanning ───────────────
def _prescan_nodes(text, brace_idx, def_map, field_use_positions):
    """
    One global pass over the text collecting every node and USE position.
    Returns sorted numpy arrays so _direct_children can binary-search instead
    of calling regex.search on every loop iteration (O(N×depth) → O(N+log N)).
    """
    logger.info('Pre-scanning node positions…')
    entries = []  # (pos, node_type, content_start, content_end, is_use)

    for m in _DIRECT_RE.finditer(text):
        bo = text.find('{', m.start())
        if bo == -1:
            continue
        bc = _brace_close(brace_idx, bo)
        if bc is None:
            continue
        entries.append((m.start(), m.group(1), bo + 1, bc, False))

    for m in _USE_STANDALONE_RE.finditer(text):
        if m.start() in field_use_positions:
            continue
        entry = def_map.get(m.group(1))
        if entry is None:
            continue
        node_type, dcs, dce = entry
        entries.append((m.start(), node_type, dcs, dce, True))

    entries.sort(key=lambda x: x[0])
    n = len(entries)
    positions  = np.array([e[0] for e in entries], dtype=np.int64)
    cs_arr     = np.array([e[2] for e in entries], dtype=np.int64)
    ce_arr     = np.array([e[3] for e in entries], dtype=np.int64)
    is_use_arr = np.array([e[4] for e in entries], dtype=bool)
    node_types = [e[1] for e in entries]
    logger.info('Pre-scan complete: %d entries', n)
    return positions, node_types, cs_arr, ce_arr, is_use_arr


# ── Direct-child iterator (O(log N) per scope via binary search) ───────────────
def _direct_children(prescan, scope_cs, scope_ce):
    positions, node_types, cs_arr, ce_arr, is_use_arr = prescan
    idx = int(np.searchsorted(positions, scope_cs))
    while idx < len(positions):
        pos = int(positions[idx])
        if pos >= scope_ce:
            break
        ncs    = int(cs_arr[idx])
        nce    = int(ce_arr[idx])
        nt     = node_types[idx]
        is_use = bool(is_use_arr[idx])
        if is_use:
            yield nt, pos, ncs, nce
            idx += 1
        else:
            if nce > scope_ce:
                idx += 1  # spans beyond scope — not a direct child
                continue
            yield nt, pos, ncs, nce
            # Jump past all grandchildren nested inside this child
            idx = int(np.searchsorted(positions, nce + 1))


# ── Inline coord resolver ──────────────────────────────────────────────────────
def _inline_coord(text, ncs, nce, brace_idx, def_map):
    cm = _COORD_FIELD_RE.search(text, ncs, nce)
    if cm:
        bo = text.find('{', cm.start())
        if bo != -1 and bo < nce:
            bc = _brace_close(brace_idx, bo)
            if bc is not None and bc <= nce:
                pm = _PT_RE.search(text, bo + 1, bc)
                if pm:
                    pp = _bracket_pos(text, pm.start(), bc)
                    if pp:
                        floats = _parse_floats(text, pp)
                        if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                            return floats.reshape(-1, 3)
    um = _COORD_USE_RE.search(text, ncs, nce)
    if um:
        entry = def_map.get(um.group(1))
        if entry:
            _, dcs, dce = entry
            pm = _PT_RE.search(text, dcs, dce)
            if pm:
                pp = _bracket_pos(text, pm.start(), dce)
                if pp:
                    floats = _parse_floats(text, pp)
                    if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                        return floats.reshape(-1, 3)
    return None


# ── Mesh collector: cache + merge-as-you-go ───────────────────────────────────
class _MeshCollector:
    """
    Accumulates geometry across millions of USE instances without creating
    individual trimesh objects per instance.

    - Caches (vertices, faces) by content-range key — parses each unique DEF once.
    - Groups accumulated arrays by color, so _merge_by_color isn't needed.
    - Caps total faces at max_faces to prevent runaway memory for huge assemblies.
    - Per-DEF instance cap (max_instances_per_def) prevents one repeated tiny
      part from consuming the entire face budget.
    """

    def __init__(self, max_faces, max_instances_per_def=50000):
        self.max_faces             = max_faces
        self.max_instances_per_def = max_instances_per_def
        self.total_faces           = 0
        # color_key → {'verts': [np arrays], 'faces': [np arrays], 'offset': int}
        self._groups    = {}
        # (ncs, nce) → (vertices_array, faces_array) or None if unparseable
        self._geo_cache = {}
        # (ncs, nce) → instance count (for per-DEF cap)
        self._inst_cnt  = {}
        self.skipped    = 0

    def _parse_geo(self, text, ncs, nce, brace_idx, def_map, parent_coord):
        key = (ncs, nce)
        cached = self._geo_cache.get(key, 'MISS')
        if cached != 'MISS':
            if cached is None:
                return None
            cached_coord, cached_faces = cached
            if cached_coord is not None:
                # inline coord — stable, reuse directly
                return cached
            # no inline coord: faces are cached but coord comes from caller
            if parent_coord is None:
                return None
            valid = np.all((cached_faces >= 0) & (cached_faces < len(parent_coord)), axis=1)
            faces = cached_faces[valid]
            return (parent_coord, faces) if len(faces) > 0 else None

        ifs_coord = _inline_coord(text, ncs, nce, brace_idx, def_map)
        ci_m = _CI_RE.search(text, ncs, nce)
        if ci_m is None:
            self._geo_cache[key] = None
            return None
        ci_pp = _bracket_pos(text, ci_m.start(), nce)
        if ci_pp is None:
            self._geo_cache[key] = None
            return None
        indices = _parse_face_indices(text, ci_pp[0], ci_pp[1])
        if len(indices) == 0:
            self._geo_cache[key] = None
            return None
        faces = _build_faces(indices)
        if len(faces) == 0:
            self._geo_cache[key] = None
            return None

        if ifs_coord is not None:
            # inline coord: cache coord+faces together (stable across calls)
            valid = np.all((faces >= 0) & (faces < len(ifs_coord)), axis=1)
            faces = faces[valid]
            if len(faces) == 0:
                self._geo_cache[key] = None
                return None
            result = (ifs_coord, faces)
            self._geo_cache[key] = result
            if len(self._geo_cache) <= 3:
                logger.info('Geo cache #%d (inline): %d verts %d faces',
                            len(self._geo_cache), len(ifs_coord), len(faces))
            return result
        else:
            # no inline coord: cache only faces (coord varies per caller)
            self._geo_cache[key] = (None, faces)
            if parent_coord is None:
                return None
            valid = np.all((faces >= 0) & (faces < len(parent_coord)), axis=1)
            faces = faces[valid]
            if len(faces) == 0:
                return None
            if len(self._geo_cache) <= 3:
                logger.info('Geo cache #%d (parent coord): %d verts %d faces',
                            len(self._geo_cache), len(parent_coord), len(faces))
            return (parent_coord, faces)

    def add_ifs(self, text, ncs, nce, brace_idx, def_map, transform, color, parent_coord):
        if self.total_faces >= self.max_faces:
            self.skipped += 1
            return False
        key = (ncs, nce)
        cnt = self._inst_cnt.get(key, 0)
        if cnt >= self.max_instances_per_def:
            self.skipped += 1
            return True
        self._inst_cnt[key] = cnt + 1

        geo = self._parse_geo(text, ncs, nce, brace_idx, def_map, parent_coord)
        if geo is None:
            return True
        verts, faces = geo

        # Apply transform — GL convention (translation in last column): h @ M.T
        if not np.allclose(transform, np.eye(4)):
            h = np.hstack([verts, np.ones((len(verts), 1), dtype=np.float64)])
            verts = (h @ transform.T)[:, :3]
            if len(self._geo_cache) <= 2:
                logger.info('IFS transform: v[0] %s -> %s',
                            geo[0][0].tolist(), verts[0].tolist())
        else:
            verts = verts.copy()

        color_key = tuple(color)
        if color_key not in self._groups:
            self._groups[color_key] = {'verts': [], 'faces': [], 'offset': 0}
        g = self._groups[color_key]
        g['faces'].append(faces + g['offset'])
        g['verts'].append(verts)
        g['offset'] += len(verts)
        self.total_faces += len(faces)
        return True

    def finalize(self):
        import trimesh
        result = []
        for color_key, g in self._groups.items():
            if not g['verts']:
                continue
            verts = np.concatenate(g['verts'])
            faces = np.concatenate(g['faces'])
            try:
                m = trimesh.Trimesh(vertices=verts, faces=faces, process=True)
            except Exception:
                m = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
            m.visual.face_colors = list(color_key)
            result.append(m)
        total_f = sum(len(m.faces) for m in result)
        total_v = sum(len(m.vertices) for m in result)
        logger.info('Collector: %d color groups, %d faces, %d verts (skipped %d over-limit)',
                    len(result), total_f, total_v, self.skipped)
        return result



_CONTAINERS_V1 = frozenset({'Separator', 'Group', 'Switch', 'TransformSeparator'})


def _walk(text, collector, brace_idx, def_map, field_use_positions,
          color_mode, prescan, progress_cb=None):
    stack = [(0, len(text), np.eye(4), None, list(_DEFAULT_COLOR))]
    total_tried = 0
    text_len = len(text)
    _mtx_parsed = [0]   # MatrixTransform nodes successfully parsed
    _mtx_failed = [0]   # MatrixTransform nodes where regex failed (comma issue?)

    while stack:
        cs, ce, parent_matrix, parent_coord, parent_color = stack.pop()

        if collector.total_faces >= collector.max_faces:
            collector.skipped += 1
            continue

        if progress_cb:
            progress_cb(cs, text_len)

        current_matrix = np.copy(parent_matrix)
        current_coord  = parent_coord
        current_color  = list(parent_color)

        for node, node_pos, ncs, nce in _direct_children(prescan, cs, ce):

            if node == 'MatrixTransform':
                vm = _MTX_VALS_RE.search(text, ncs, nce)
                if vm:
                    mat = np.array([float(vm.group(i)) for i in range(1, 17)],
                                   dtype=np.float64).reshape(4, 4)
                    if not hasattr(_walk, '_logged_mtx'):
                        _walk._logged_mtx = True
                        logger.info('FIRST MatrixTransform raw 4x4:\n%s', mat)
                        logger.info('  row3(OI translation?) = %s', mat[3, :3])
                        logger.info('  col3(GL translation?) = %s', mat[:3, 3])
                        logger.info('  raw text snippet: %r', text[ncs:min(ncs+120, nce)])
                    _mtx_parsed[0] += 1
                    current_matrix = current_matrix @ mat
                else:
                    _mtx_failed[0] += 1
                    if _mtx_failed[0] <= 3:
                        logger.warning('MatrixTransform regex FAILED — snippet: %r',
                                       text[ncs:min(ncs+120, nce)])
                stack.append((ncs, nce, current_matrix, current_coord, list(current_color)))

            elif node == 'Transform':
                hdr = text[ncs: min(ncs + 600, nce)]
                M = np.eye(4)
                tr = _TR1_RE.search(hdr)
                if tr:
                    M[0,3]=float(tr.group(1)); M[1,3]=float(tr.group(2)); M[2,3]=float(tr.group(3))
                ro = _RO1_RE.search(hdr)
                if ro:
                    M = M @ _axis_angle_to_mat4(float(ro.group(1)), float(ro.group(2)),
                                                float(ro.group(3)), float(ro.group(4)))
                sc = _SC1_RE.search(hdr)
                if sc:
                    sx = float(sc.group(1))
                    sy = float(sc.group(2)) if sc.group(2) else sx
                    sz = float(sc.group(3)) if sc.group(3) else sx
                    M = M @ np.diag([sx, sy, sz, 1.0])
                current_matrix = current_matrix @ M         # accumulate, not reset
                stack.append((ncs, nce, current_matrix, current_coord, list(current_color)))

            elif node == 'Coordinate3':
                pt_m = _PT_RE.search(text, ncs, nce)
                if pt_m:
                    pp = _bracket_pos(text, pt_m.start(), nce)
                    if pp:
                        floats = _parse_floats(text, pp)
                        if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                            current_coord = floats.reshape(-1, 3)

            elif node == 'Coordinate':
                pt_m = _PT_RE.search(text, ncs, nce)
                if pt_m:
                    pp = _bracket_pos(text, pt_m.start(), nce)
                    if pp:
                        floats = _parse_floats(text, pp)
                        if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                            current_coord = floats.reshape(-1, 3)

            elif node == 'Material':
                col = _extract_diffuse(text, ncs, nce)
                if col:
                    current_color = col

            elif node == 'Appearance':
                for child_node, _, ccs, cce in _direct_children(prescan, ncs, nce):
                    if child_node == 'Material':
                        col = _extract_diffuse(text, ccs, cce)
                        if col:
                            current_color = col
                        break

            elif node in _CONTAINERS_V1:
                stack.append((ncs, nce, current_matrix, current_coord, list(current_color)))

            elif node == 'Shape':
                shape_color = list(current_color)
                shape_coord = current_coord
                for child_node, _, ccs, cce in _direct_children(prescan, ncs, nce):
                    if child_node == 'Appearance':
                        for sub, _, scs, sce in _direct_children(prescan, ccs, cce):
                            if sub == 'Material':
                                col = _extract_diffuse(text, scs, sce)
                                if col:
                                    shape_color = col
                                break
                    elif child_node == 'Coordinate':
                        pt_m = _PT_RE.search(text, ccs, cce)
                        if pt_m:
                            pp = _bracket_pos(text, pt_m.start(), cce)
                            if pp:
                                floats = _parse_floats(text, pp)
                                if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                                    shape_coord = floats.reshape(-1, 3)
                    elif child_node == 'IndexedFaceSet':
                        total_tried += 1
                        color = list(_DEFAULT_COLOR) if color_mode == 'gray' else list(shape_color)
                        collector.add_ifs(text, ccs, cce, brace_idx, def_map,
                                          current_matrix, color, shape_coord)

            elif node == 'LOD':
                for child_node, _, ccs, cce in _direct_children(prescan, ncs, nce):
                    if child_node in _CONTAINERS_V1 or child_node in ('LOD', 'Transform'):
                        stack.append((ccs, cce, current_matrix, current_coord, list(current_color)))
                        break

            elif node == 'IndexedFaceSet':
                total_tried += 1
                color = list(_DEFAULT_COLOR) if color_mode == 'gray' else list(current_color)
                collector.add_ifs(text, ncs, nce, brace_idx, def_map,
                                  current_matrix, color, current_coord)

    logger.info('IFS tried=%d  MatrixTransform: parsed=%d failed=%d',
                total_tried, _mtx_parsed[0], _mtx_failed[0])


# ── Post-processing ────────────────────────────────────────────────────────────
def _merge_by_color(meshes):
    """Group by color, concatenate, deduplicate vertices (big size reduction for VRML)."""
    import trimesh
    groups = {}
    for m in meshes:
        fc = m.visual.face_colors
        c = fc[0] if (hasattr(fc, '__len__') and len(fc) > 0) else _DEFAULT_COLOR
        key = tuple(int(x) for x in c[:4])
        groups.setdefault(key, []).append(m)

    result = []
    for key, group in groups.items():
        merged = trimesh.util.concatenate(group) if len(group) > 1 else group[0].copy()
        # Deduplicate shared vertices — VRML duplicates every vert per face
        try:
            merged = trimesh.Trimesh(vertices=merged.vertices, faces=merged.faces, process=True)
        except Exception:
            pass
        merged.visual.face_colors = list(key)
        result.append(merged)

    total_f = sum(len(m.faces) for m in result)
    total_v = sum(len(m.vertices) for m in result)
    logger.info('Merged: %d color groups, %d faces, %d verts', len(result), total_f, total_v)
    return result


def _simplify(mesh, max_faces):
    if len(mesh.faces) <= max_faces:
        return mesh
    logger.info('Simplifying %d → ~%d faces …', len(mesh.faces), max_faces)
    for method in ('simplify_quadric_decimation', 'simplify_quadratic_decimation'):
        if hasattr(mesh, method):
            try:
                r = getattr(mesh, method)(max_faces)
                logger.info('After simplification: %d faces', len(r.faces))
                return r
            except Exception as exc:
                logger.warning('Simplification skipped: %s', exc)
                return mesh
    return mesh


# ── VRML parse entry point ─────────────────────────────────────────────────────
def _parse_vrml(src: Path, color_mode: str, max_faces: int = 500_000, progress_cb=None):
    import trimesh
    logger.info('Reading %s (%.1f MB) …', src.name, src.stat().st_size / 1e6)
    text = src.read_text(encoding='utf-8', errors='replace')

    if progress_cb:
        progress_cb(5, 100, 'Building index…')
    brace_idx           = _build_brace_index(text)
    # bracket index built locally per-node — avoids scanning 1 GB for [] pairs
    def_map             = _build_def_map(text, brace_idx)
    field_use_positions = _build_field_use_positions(text)

    if progress_cb:
        progress_cb(10, 100, 'Pre-scanning nodes…')
    prescan = _prescan_nodes(text, brace_idx, def_map, field_use_positions)

    if progress_cb:
        progress_cb(15, 100, 'Parsing geometry…')

    collector = _MeshCollector(max_faces=max_faces)
    last_pct  = [15]
    max_pos   = [0]

    def _walker_cb(pos, total):
        if pos > max_pos[0]:
            max_pos[0] = pos
        pct = int(15 + 65 * max_pos[0] / max(total, 1))
        if pct > last_pct[0]:
            last_pct[0] = pct
            if progress_cb:
                progress_cb(pct, 100, f'Parsing… {pct}%')

    _walk(text, collector, brace_idx, def_map, field_use_positions,
          color_mode, prescan=prescan, progress_cb=_walker_cb)

    sample_colors = list(collector._groups.keys())[:5]
    logger.info('Walk done: total_faces=%d geo_cache=%d groups=%d skipped=%d colors=%s',
                collector.total_faces, len(collector._geo_cache),
                len(collector._groups), collector.skipped, sample_colors)

    if progress_cb:
        progress_cb(82, 100, 'Finalizing meshes…')
    meshes = collector.finalize()
    if not meshes:
        raise ValueError('No geometry found in file.')
    logger.info('Final: %d color groups', len(meshes))
    return meshes

def _build_glb_scene(meshes):
    """
    Export each color-group mesh as a named node with a PBR material.
    baseColorFactor carries the color — Three.js reads this natively.
    metallicFactor=0 avoids the dark metallic default.
    Falls back to plain concatenated export if PBRMaterial unavailable.
    """
    import trimesh
    try:
        from trimesh.visual.material import PBRMaterial
    except ImportError:
        combined = trimesh.util.concatenate(meshes)
        raw = combined.export(file_type='glb')
        return bytes(raw) if not isinstance(raw, bytes) else raw

    scene = trimesh.scene.Scene()
    for i, m in enumerate(meshes):
        fc = m.visual.face_colors
        c = list(fc[0]) if (hasattr(fc, '__len__') and len(fc) > 0) else list(_DEFAULT_COLOR)
        r, g, b, a = (c + [255])[:4]
        mat = PBRMaterial(
            baseColorFactor=[r/255.0, g/255.0, b/255.0, a/255.0],
            metallicFactor=0.0,
            roughnessFactor=0.8,
        )
        m2 = trimesh.Trimesh(vertices=m.vertices, faces=m.faces, process=False)
        m2.visual = trimesh.visual.TextureVisuals(material=mat)
        scene.add_geometry(m2, node_name=f'part_{i}')

    raw = scene.export(file_type='glb')
    return bytes(raw) if not isinstance(raw, bytes) else raw


# ── Background job ─────────────────────────────────────────────────────────────
def _run_job(jid, tmp_path, out_format, max_faces, color_mode):
    try:
        import trimesh

        last_pct = [0]
        def cb(cur, total, label='Converting…'):
            pct = max(cur if total == 100 else int(cur / max(total, 1) * 100), last_pct[0])
            last_pct[0] = pct
            _job_set(jid, pct=pct, label=label)

        meshes = _parse_vrml(tmp_path, color_mode, max_faces=max_faces, progress_cb=cb)

        _job_set(jid, pct=93, label=f'Exporting {out_format.upper()}…')

        if out_format == 'glb':
            raw = _build_glb_scene(meshes)
        else:
            import trimesh as _trimesh
            combined = _trimesh.util.concatenate(meshes)
            combined = _simplify(combined, max_faces)
            raw = combined.export(file_type=out_format)

        out_bytes = bytes(raw) if not isinstance(raw, bytes) else raw

        _job_set(jid, pct=100, label='Done!', status='done',
                 result=out_bytes, parts=len(meshes), size=len(out_bytes))
        logger.info('Job %s done: %.2f MB', jid, len(out_bytes) / 1e6)

    except Exception:
        logger.error('Job %s failed:\n%s', jid, traceback.format_exc())
        _job_set(jid, status='error', error='Conversion failed — check server log.')
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


# ── Flask routes ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/start', methods=['POST'])
def start():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(detail='No file received.'), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('wrl', 'vrml'):
        return jsonify(detail=f'Only .wrl/.vrml supported (got .{ext}).'), 400
    out_format = request.form.get('out_format', 'glb').lower()
    if out_format not in ('glb', 'obj', 'stl'):
        out_format = 'glb'
    max_faces  = max(5000, min(int(request.form.get('max_faces', 5_000_000)), 100_000_000))
    color_mode = request.form.get('color_mode', 'actual')
    if color_mode not in ('actual', 'gray'):
        color_mode = 'actual'

    with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
        tmp_path = Path(tmp.name)
        f.save(tmp)

    jid = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[jid] = {'status': 'running', 'pct': 2, 'label': 'Queued…',
                      'result': None, 'format': out_format,
                      'parts': 0, 'size': 0, 'error': ''}
    threading.Thread(target=_run_job,
                     args=(jid, tmp_path, out_format, max_faces, color_mode),
                     daemon=True).start()
    return jsonify(job_id=jid)


@app.route('/progress/<jid>')
def progress(jid):
    def generate():
        while True:
            job = _job_get(jid)
            if not job:
                yield f'data: {json.dumps({"status":"error","error":"Job not found","pct":0,"label":"Error"})}\n\n'
                return
            payload = {
                'pct':    job.get('pct', 0),
                'label':  job.get('label', '…'),
                'status': job.get('status', 'running'),
                'parts':  job.get('parts', 0),
                'size':   job.get('size', 0),
                'error':  job.get('error', ''),
            }
            yield f'data: {json.dumps(payload)}\n\n'
            if job.get('status') in ('done', 'error'):
                return
            time.sleep(0.4)
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/download/<jid>')
def download(jid):
    job = _job_get(jid)
    if not job or job.get('status') != 'done':
        return jsonify(detail='Not ready.'), 404
    fmt  = job.get('format', 'glb')
    mime = {'glb': 'model/gltf-binary', 'obj': 'text/plain',
            'stl': 'application/octet-stream'}.get(fmt, 'application/octet-stream')
    data = job['result']
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid]['result'] = None
    return send_file(io.BytesIO(data), mimetype=mime,
                     as_attachment=True, download_name=f'model.{fmt}')


if __name__ == '__main__':
    print('\n  VRML / WRL Converter  →  http://localhost:5555\n')
    app.run(host='0.0.0.0', port=5555, debug=False, threaded=True)
