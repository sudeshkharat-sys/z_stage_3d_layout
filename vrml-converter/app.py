"""VRML 1.0 + 2.0 / WRL → GLB / OBJ / STL Converter

Handles:
  - VRML 1.0 (Open Inventor) state machine: Coordinate3 + Material + IndexedFaceSet siblings
  - VRML 2.0 (VRML97): Transform/Shape/Appearance/Material/Coordinate hierarchy
  - DEF/USE references (named node reuse)
  - Same-color mesh merging for compact GLB output
  - Draco geometry compression (KHR_draco_mesh_compression) via DracoPy
  - Server-Sent Events (SSE) real progress stream
  - Color mode: actual VRML colors or uniform light-gray

Usage:
    pip install flask trimesh[easy] numpy DracoPy
    python app.py
Open http://localhost:5555
"""

import io
import json
import logging
import re
import struct
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
_jobs = {}   # job_id -> {"status": "running"|"done"|"error", "pct": int, "label": str,
             #             "result": bytes|None, "format": str, "error": str}
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
  .badge { display: inline-block; font-size: .72rem; padding: .1rem .45rem; border-radius: 4px;
    background: #164e63; color: #67e8f9; margin-left: .4rem; vertical-align: middle; }
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
      <label>3. Max faces</label>
      <input type="number" id="maxFaces" value="500000" min="5000" max="10000000" step="50000"/>
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
      let badge=d.draco?'<span class="badge">Draco</span>':'';
      rb.innerHTML='&#9989; Converted!'+badge+'<div class="stats">'+
        'Output size: '+fmt(d.size)+'<br/>'+
        'Parts: '+d.parts+'<br/>'+
        (d.draco?'Compression: Draco (KHR_draco_mesh_compression)<br/>':'')+
        'Color mode: '+(colorMode==='actual'?'Actual VRML colors':'Uniform light gray')+
        '</div>';
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

# VRML 1.0 + 2.0 node types we scan for
_DIRECT_RE = re.compile(
    r'\b(MatrixTransform|Transform|Separator|Group|Switch'
    r'|TransformSeparator|Coordinate3|IndexedFaceSet|IndexedLineSet'
    r'|Material|MaterialBinding|Normal|NormalBinding|ShapeHints'
    r'|Info|Texture2|Texture2Transform|TextureCoordinate2'
    r'|PointLight|DirectionalLight|SpotLight|OrthographicCamera|PerspectiveCamera'
    r'|Cube|Sphere|Cone|Cylinder|AsciiText'
    r'|FontStyle|DrawStyle|LightModel|BaseColor|PackedColor'
    r'|EnvironmentMap|ShapeKit|WWWAnchor|WWWInline'
    # VRML 2.0
    r'|Shape|Appearance|Coordinate|Anchor|Billboard|Collision'
    r')\s*\{'
)

# DEF name NodeType {
_DEF_RE = re.compile(r'\bDEF\s+(\S+)\s+(\w+)\s*\{')
# standalone USE name (not after coord/appearance/geometry/material field names)
_USE_STANDALONE_RE = re.compile(r'(?<!\w)USE\s+(\S+)')
# field-value USE patterns: coord USE x, appearance USE x, geometry USE x, etc.
_FIELD_USE_RE = re.compile(
    r'\b(?:coord|appearance|geometry|material|color|normal|texCoord|children)\s+USE\s+(\S+)'
)

_PT_RE       = re.compile(r'\bpoint\s*\[')
_CI_RE       = re.compile(r'\bcoordIndex\s*\[')
_DC_RE       = re.compile(rf'\bdiffuseColor\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_FLT16       = r'\s+'.join([rf'({_FLT})'] * 16)
_MTX_VALS_RE = re.compile(r'\bmatrix\s+' + _FLT16)
_TR1_RE      = re.compile(rf'\btranslation\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_SC1_RE      = re.compile(rf'\bscaleFactor\s+({_FLT})(?:\s+({_FLT})\s+({_FLT}))?')
_RO1_RE      = re.compile(rf'\brotation\s+({_FLT})\s+({_FLT})\s+({_FLT})\s+({_FLT})')
# Inline coord: coord [DEF name] Coordinate { point [...] }
_COORD_FIELD_RE = re.compile(r'\bcoord\s+(?:DEF\s+\S+\s+)?Coordinate\s*\{')
_COORD_USE_RE   = re.compile(r'\bcoord\s+USE\s+(\S+)')

_DEFAULT_COLOR = (230, 230, 230, 255)


# ── Index builders ─────────────────────────────────────────────────────────────
def _build_brace_index(text):
    logger.info('Building brace index …')
    idx, stack = {}, []
    for m in _BRACE_RE.finditer(text):
        if m.group() == '{':
            stack.append(m.start())
        elif stack:
            idx[stack.pop()] = m.start()
    logger.info('Brace index: %d pairs', len(idx))
    return idx


def _build_bracket_index(text):
    idx, stack = {}, []
    for m in _BRACKET_RE.finditer(text):
        if m.group() == '[':
            stack.append(m.start())
        elif stack:
            idx[stack.pop()] = m.start()
    return idx


def _build_def_map(text, brace_idx):
    """Pre-scan all DEF name NodeType { ... } entries."""
    def_map = {}
    for m in _DEF_RE.finditer(text):
        name, node_type = m.group(1), m.group(2)
        bo = text.find('{', m.start())
        if bo == -1:
            continue
        bc = brace_idx.get(bo)
        if bc is not None:
            def_map[name] = (node_type, bo + 1, bc)
    logger.info('DEF map: %d named nodes', len(def_map))
    return def_map


def _build_field_use_positions(text):
    """Pre-compute byte offsets of all field-value USE tokens (coord USE x, etc.)."""
    positions = set()
    for m in _FIELD_USE_RE.finditer(text):
        # find the "USE" within this match
        use_pos = text.index('USE', m.start())
        positions.add(use_pos)
    return positions


# ── Position helpers ───────────────────────────────────────────────────────────
def _bracket_pos(text, start, end, bracket_idx):
    p = text.find('[', start)
    if p == -1 or p >= end:
        return None
    cl = bracket_idx.get(p)
    return (p + 1, cl) if (cl is not None and cl <= end) else None


# ── Math helpers ───────────────────────────────────────────────────────────────
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


# ── Face builder ───────────────────────────────────────────────────────────────
def _build_faces(indices):
    faces, fan = [], []
    for idx in indices:
        if idx < 0:
            if len(fan) >= 3:
                for j in range(1, len(fan) - 1):
                    faces.append((fan[0], fan[j], fan[j + 1]))
            fan = []
        else:
            fan.append(idx)
    if len(fan) >= 3:
        for j in range(1, len(fan) - 1):
            faces.append((fan[0], fan[j], fan[j + 1]))
    return np.array(faces, dtype=np.int64) if faces else np.empty((0, 3), dtype=np.int64)


# ── Direct-child scanner ───────────────────────────────────────────────────────
def _direct_children(text, cs, ce, brace_idx, def_map, field_use_positions):
    """
    Yield (node_name, keyword_pos, content_cs, content_ce) for every direct
    child in [cs, ce).  Also yields bare USE references that are NOT field-
    value USEs (i.e. standalone child nodes referenced by name).
    """
    pos = cs
    while pos < ce:
        # find next explicit node OR USE reference
        nm = _DIRECT_RE.search(text, pos, ce)
        um = _USE_STANDALONE_RE.search(text, pos, ce)

        # pick whichever comes first
        if nm is None and um is None:
            break
        use_first = (nm is None) or (um is not None and um.start() < nm.start())

        if use_first:
            # Check it's not a field-value USE
            use_token_pos = um.start()
            if use_token_pos in field_use_positions:
                pos = um.end()
                continue
            name = um.group(1)
            entry = def_map.get(name)
            if entry is None:
                pos = um.end()
                continue
            node_type, dcs, dce = entry
            yield node_type, um.start(), dcs, dce
            pos = um.end()
        else:
            node = nm.group(1)
            brace_open = text.find('{', nm.start())
            if brace_open == -1 or brace_open >= ce:
                pos = nm.end()
                continue
            brace_close = brace_idx.get(brace_open)
            if brace_close is None or brace_close > ce:
                pos = nm.end()
                continue
            # Check for DEF prefix before this node
            prefix = text[max(cs, nm.start() - 40): nm.start()]
            def_prefix = re.search(r'\bDEF\s+(\S+)\s*$', prefix)
            if def_prefix:
                def_name = def_prefix.group(1)
                if def_name not in def_map:
                    def_map[def_name] = (node, brace_open + 1, brace_close)
            yield node, nm.start(), brace_open + 1, brace_close
            pos = brace_close + 1


# ── Inline coord resolver ──────────────────────────────────────────────────────
def _inline_coord(text, ncs, nce, brace_idx, bracket_idx, def_map):
    """
    For VRML 2.0 IFS: resolve `coord Coordinate { point [...] }` or
    `coord USE name` inline within an IFS block.
    Returns numpy (N,3) or None.
    """
    # inline Coordinate node
    cm = _COORD_FIELD_RE.search(text, ncs, nce)
    if cm:
        bo = text.find('{', cm.start())
        if bo != -1 and bo < nce:
            bc = brace_idx.get(bo)
            if bc is not None and bc <= nce:
                pm = _PT_RE.search(text, bo + 1, bc)
                if pm:
                    pp = _bracket_pos(text, pm.start(), bc, bracket_idx)
                    if pp:
                        floats = _parse_floats(text, pp)
                        if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                            return floats.reshape(-1, 3)
    # USE reference
    um = _COORD_USE_RE.search(text, ncs, nce)
    if um:
        entry = def_map.get(um.group(1))
        if entry:
            _, dcs, dce = entry
            pm = _PT_RE.search(text, dcs, dce)
            if pm:
                pp = _bracket_pos(text, pm.start(), dce, bracket_idx)
                if pp:
                    floats = _parse_floats(text, pp)
                    if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                        return floats.reshape(-1, 3)
    return None


# ── Color extractor ────────────────────────────────────────────────────────────
def _extract_diffuse(text, cs, ce):
    hdr = text[cs: min(cs + 400, ce)]
    dc = _DC_RE.search(hdr)
    if dc:
        return [int(float(dc.group(i)) * 255) for i in (1, 2, 3)] + [255]
    return None


# ── VRML 1.0 + 2.0 state-machine walker ───────────────────────────────────────
_CONTAINERS_V1 = frozenset({
    'Separator', 'Group', 'Switch', 'TransformSeparator',
})
_CONTAINERS_V2 = frozenset({
    'Shape', 'Anchor', 'Billboard', 'Collision',
})


def _walk(text, meshes, brace_idx, bracket_idx, def_map, field_use_positions,
          color_mode, progress_cb=None):
    import trimesh

    # stack items: (cs, ce, matrix, coord, color)
    stack = [(0, len(text), np.eye(4), None, list(_DEFAULT_COLOR))]
    total_tried = 0
    text_len = len(text)

    while stack:
        cs, ce, parent_matrix, parent_coord, parent_color = stack.pop()

        if progress_cb:
            progress_cb(cs, text_len)

        current_matrix = np.copy(parent_matrix)
        current_coord  = parent_coord
        current_color  = list(parent_color)

        for node, node_pos, ncs, nce in _direct_children(
                text, cs, ce, brace_idx, def_map, field_use_positions):

            # ── Transform matrix nodes ──
            if node == 'MatrixTransform':
                vm = _MTX_VALS_RE.search(text, ncs, nce)
                if vm:
                    mat = np.array([float(vm.group(i)) for i in range(1, 17)],
                                   dtype=np.float64).reshape(4, 4)
                    current_matrix = parent_matrix @ mat
                # push children for VRML 2.0
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
                new_matrix = parent_matrix @ M
                # VRML 1.0: update current matrix for siblings
                current_matrix = new_matrix
                # VRML 2.0: push children scope
                stack.append((ncs, nce, new_matrix, current_coord, list(current_color)))

            # ── VRML 1.0 coord ──
            elif node == 'Coordinate3':
                pt_m = _PT_RE.search(text, ncs, nce)
                if pt_m:
                    pp = _bracket_pos(text, pt_m.start(), nce, bracket_idx)
                    if pp:
                        floats = _parse_floats(text, pp)
                        if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                            current_coord = floats.reshape(-1, 3)

            # ── VRML 2.0 coord (also in Coordinate node used by IFS) ──
            elif node == 'Coordinate':
                pt_m = _PT_RE.search(text, ncs, nce)
                if pt_m:
                    pp = _bracket_pos(text, pt_m.start(), nce, bracket_idx)
                    if pp:
                        floats = _parse_floats(text, pp)
                        if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                            current_coord = floats.reshape(-1, 3)

            # ── VRML 1.0 material ──
            elif node == 'Material':
                col = _extract_diffuse(text, ncs, nce)
                if col:
                    current_color = col

            # ── VRML 2.0 Appearance ──
            elif node == 'Appearance':
                for child_node, _, ccs, cce in _direct_children(
                        text, ncs, nce, brace_idx, def_map, field_use_positions):
                    if child_node == 'Material':
                        col = _extract_diffuse(text, ccs, cce)
                        if col:
                            current_color = col
                        break

            # ── VRML 1.0 containers (scope-isolated) ──
            elif node in _CONTAINERS_V1:
                stack.append((ncs, nce, current_matrix, current_coord, list(current_color)))

            # ── VRML 2.0 Shape (pulls Appearance + geometry) ──
            elif node == 'Shape':
                shape_color = list(current_color)
                shape_coord = current_coord
                for child_node, _, ccs, cce in _direct_children(
                        text, ncs, nce, brace_idx, def_map, field_use_positions):
                    if child_node == 'Appearance':
                        for sub, _, scs, sce in _direct_children(
                                text, ccs, cce, brace_idx, def_map, field_use_positions):
                            if sub == 'Material':
                                col = _extract_diffuse(text, scs, sce)
                                if col:
                                    shape_color = col
                                break
                    elif child_node == 'Coordinate':
                        pt_m = _PT_RE.search(text, ccs, cce)
                        if pt_m:
                            pp = _bracket_pos(text, pt_m.start(), cce, bracket_idx)
                            if pp:
                                floats = _parse_floats(text, pp)
                                if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                                    shape_coord = floats.reshape(-1, 3)
                    elif child_node == 'IndexedFaceSet':
                        total_tried += 1
                        # Try inline coord first
                        ifs_coord = _inline_coord(text, ccs, cce, brace_idx, bracket_idx, def_map)
                        if ifs_coord is None:
                            ifs_coord = shape_coord
                        if ifs_coord is None:
                            continue
                        ci_m = _CI_RE.search(text, ccs, cce)
                        if ci_m is None:
                            continue
                        ci_pp = _bracket_pos(text, ci_m.start(), cce, bracket_idx)
                        if ci_pp is None:
                            continue
                        indices = [int(x) for x in re.findall(r'-?\d+', text[ci_pp[0]:ci_pp[1]])]
                        if not indices:
                            continue
                        faces = _build_faces(indices)
                        if len(faces) == 0:
                            continue
                        valid = np.all((faces >= 0) & (faces < len(ifs_coord)), axis=1)
                        faces = faces[valid]
                        if len(faces) == 0:
                            continue
                        color = list(_DEFAULT_COLOR) if color_mode == 'gray' else list(shape_color)
                        mesh = trimesh.Trimesh(vertices=ifs_coord.copy(), faces=faces, process=False)
                        if not np.allclose(current_matrix, np.eye(4)):
                            mesh.apply_transform(current_matrix)
                        mesh.visual.face_colors = color
                        meshes.append(mesh)

            # ── LOD — use first child only ──
            elif node == 'LOD':
                for child_node, _, ccs, cce in _direct_children(
                        text, ncs, nce, brace_idx, def_map, field_use_positions):
                    if child_node in _CONTAINERS_V1 or child_node == 'LOD' or child_node == 'Transform':
                        stack.append((ccs, cce, current_matrix, current_coord, list(current_color)))
                        break

            # ── VRML 1.0 IndexedFaceSet ──
            elif node == 'IndexedFaceSet':
                total_tried += 1
                # try inline coord (VRML 2.0 style embedded in IFS)
                ifs_coord = _inline_coord(text, ncs, nce, brace_idx, bracket_idx, def_map)
                if ifs_coord is None:
                    ifs_coord = current_coord
                if ifs_coord is None:
                    continue

                ci_m = _CI_RE.search(text, ncs, nce)
                if ci_m is None:
                    continue
                ci_pp = _bracket_pos(text, ci_m.start(), nce, bracket_idx)
                if ci_pp is None:
                    continue

                indices = [int(x) for x in re.findall(r'-?\d+', text[ci_pp[0]:ci_pp[1]])]
                if not indices:
                    continue
                faces = _build_faces(indices)
                if len(faces) == 0:
                    continue
                valid = np.all((faces >= 0) & (faces < len(ifs_coord)), axis=1)
                faces = faces[valid]
                if len(faces) == 0:
                    continue

                color = list(_DEFAULT_COLOR) if color_mode == 'gray' else list(current_color)
                mesh = trimesh.Trimesh(vertices=ifs_coord.copy(), faces=faces, process=False)
                if not np.allclose(current_matrix, np.eye(4)):
                    mesh.apply_transform(current_matrix)
                mesh.visual.face_colors = color
                meshes.append(mesh)
                logger.info('  part %d: %d verts  %d faces  color=%s',
                            len(meshes), len(ifs_coord), len(faces), color[:3])

    logger.info('IFS tried: %d  succeeded: %d', total_tried, len(meshes))


# ── Simplification ─────────────────────────────────────────────────────────────
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


# ── Same-color mesh merging ────────────────────────────────────────────────────
def _merge_by_color(meshes):
    """Merge meshes with the same color into single meshes to reduce GLB primitive count."""
    import trimesh
    groups = {}
    for m in meshes:
        fc = m.visual.face_colors
        if hasattr(fc, '__len__') and len(fc) > 0:
            c = fc[0]
        else:
            c = list(_DEFAULT_COLOR)
        key = tuple(int(x) for x in c[:4])
        groups.setdefault(key, []).append(m)
    result = []
    for key, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
        else:
            merged = trimesh.util.concatenate(group)
            merged.visual.face_colors = list(key)
            result.append(merged)
    logger.info('Color groups: %d → %d merged meshes', len(meshes), len(result))
    return result


# ── PBR material assignment ────────────────────────────────────────────────────
def _assign_pbr_materials(meshes):
    """Assign PBR materials (metallic=0, roughness=0.7) for correct Three.js rendering."""
    try:
        from trimesh.visual.material import PBRMaterial
        result = []
        for m in meshes:
            fc = m.visual.face_colors
            if hasattr(fc, '__len__') and len(fc) > 0:
                c = list(fc[0])
            else:
                c = list(_DEFAULT_COLOR)
            r, g, b, a = (c + [255])[:4]
            mat = PBRMaterial(
                baseColorFactor=[r/255, g/255, b/255, a/255],
                metallicFactor=0.0,
                roughnessFactor=0.7,
            )
            m2 = m.copy()
            m2.visual = m2.visual.to_texture()
            m2.visual.material = mat
            result.append(m2)
        return result
    except Exception:
        return meshes


# ── Draco GLB export ───────────────────────────────────────────────────────────
def _export_glb_draco(meshes):
    """Build a GLB with KHR_draco_mesh_compression. Returns bytes or None on failure."""
    try:
        import DracoPy
    except ImportError:
        logger.warning('DracoPy not installed — falling back to standard GLB')
        return None

    try:
        import base64

        bin_chunks = []
        bin_offset = 0

        accessors = []
        buffer_views = []
        mesh_primitives = []
        materials = []

        for idx, m in enumerate(meshes):
            verts = np.array(m.vertices, dtype=np.float32)
            faces = np.array(m.faces, dtype=np.uint32)

            try:
                draco_bytes = DracoPy.encode(verts, faces)
            except Exception:
                try:
                    draco_bytes = DracoPy.encode_mesh_to_buffer(
                        verts.flatten().tolist(), faces.flatten().tolist())
                except Exception as e:
                    logger.warning('Draco encode failed for mesh %d: %s', idx, e)
                    return None

            # Buffer view for Draco blob
            bv_idx = len(buffer_views)
            buffer_views.append({
                "buffer": 0,
                "byteOffset": bin_offset,
                "byteLength": len(draco_bytes),
            })
            bin_chunks.append(draco_bytes)
            bin_offset += len(draco_bytes)
            # Pad to 4-byte alignment
            pad = (4 - len(draco_bytes) % 4) % 4
            if pad:
                bin_chunks.append(b'\x00' * pad)
                bin_offset += pad

            # Position accessor (placeholder — Draco loader fills actual values)
            vmin = verts.min(axis=0).tolist()
            vmax = verts.max(axis=0).tolist()
            pos_acc = len(accessors)
            accessors.append({
                "bufferView": bv_idx,
                "componentType": 5126,  # FLOAT
                "count": len(verts),
                "type": "VEC3",
                "min": vmin,
                "max": vmax,
            })
            idx_acc = len(accessors)
            accessors.append({
                "bufferView": bv_idx,
                "componentType": 5125,  # UNSIGNED_INT
                "count": int(faces.size),
                "type": "SCALAR",
            })

            # Material
            fc = m.visual.face_colors if hasattr(m.visual, 'face_colors') else None
            if fc is not None and hasattr(fc, '__len__') and len(fc) > 0:
                c = list(fc[0])
            else:
                c = list(_DEFAULT_COLOR)
            r, g, b, a = (c + [255])[:4]
            mat_idx = len(materials)
            materials.append({
                "pbrMetallicRoughness": {
                    "baseColorFactor": [r/255, g/255, b/255, a/255],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.7,
                },
                "doubleSided": True,
            })

            mesh_primitives.append({
                "attributes": {"POSITION": pos_acc},
                "indices": idx_acc,
                "material": mat_idx,
                "extensions": {
                    "KHR_draco_mesh_compression": {
                        "bufferView": bv_idx,
                        "attributes": {"POSITION": 0},
                    }
                },
            })

        bin_data = b''.join(bin_chunks)

        gltf = {
            "asset": {"version": "2.0", "generator": "vrml-converter-draco"},
            "extensionsUsed": ["KHR_draco_mesh_compression"],
            "extensionsRequired": ["KHR_draco_mesh_compression"],
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": mesh_primitives}],
            "materials": materials,
            "accessors": accessors,
            "bufferViews": buffer_views,
            "buffers": [{"byteLength": len(bin_data)}],
        }

        json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
        # pad JSON to 4-byte boundary
        json_pad = (4 - len(json_bytes) % 4) % 4
        json_bytes += b' ' * json_pad

        # GLB structure: header(12) + JSON chunk(8+N) + BIN chunk(8+M)
        glb_len = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
        header  = struct.pack('<III', 0x46546C67, 2, glb_len)
        json_chunk = struct.pack('<II', len(json_bytes), 0x4E4F534A) + json_bytes
        bin_chunk  = struct.pack('<II', len(bin_data), 0x004E4942) + bin_data

        return header + json_chunk + bin_chunk

    except Exception as exc:
        logger.error('Draco GLB export failed: %s', exc)
        return None


# ── GLB export (Draco → standard fallback) ────────────────────────────────────
def _export_glb(meshes):
    """Export list of meshes as GLB. Tries Draco first, falls back to trimesh Scene."""
    draco_bytes = _export_glb_draco(meshes)
    if draco_bytes:
        return draco_bytes, True

    # Standard trimesh GLB with PBR materials
    import trimesh
    pbr_meshes = _assign_pbr_materials(meshes)
    scene = trimesh.scene.Scene()
    for i, m in enumerate(pbr_meshes):
        scene.add_geometry(m, node_name=f'part_{i}')
    raw = scene.export(file_type='glb')
    return bytes(raw) if not isinstance(raw, bytes) else raw, False


# ── VRML parse entry point ─────────────────────────────────────────────────────
def _parse_vrml(src: Path, color_mode: str, progress_cb=None):
    import trimesh
    logger.info('Reading %s (%.1f MB) …', src.name, src.stat().st_size / 1e6)
    text = src.read_text(encoding='utf-8', errors='replace')
    if progress_cb:
        progress_cb(0, 10, 'Building index…')
    brace_idx   = _build_brace_index(text)
    bracket_idx = _build_bracket_index(text)
    def_map     = _build_def_map(text, brace_idx)
    field_use_positions = _build_field_use_positions(text)
    if progress_cb:
        progress_cb(1, 10, 'Parsing geometry…')
    meshes = []
    text_len = len(text)

    last_pct = [0]
    def _walker_cb(pos, total):
        pct = int(10 + 70 * pos / max(total, 1))
        if pct != last_pct[0]:
            last_pct[0] = pct
            if progress_cb:
                progress_cb(pct, 100, f'Parsing… {pct}%')

    _walk(text, meshes, brace_idx, bracket_idx, def_map, field_use_positions,
          color_mode, progress_cb=_walker_cb)
    if not meshes:
        raise ValueError('No geometry found — check terminal for details.')
    if progress_cb:
        progress_cb(80, 100, 'Merging meshes…')
    meshes = _merge_by_color(meshes)
    logger.info('After merge: %d mesh groups', len(meshes))
    return meshes


# ── Background conversion job ──────────────────────────────────────────────────
def _run_job(jid, tmp_path, out_format, max_faces, color_mode):
    try:
        def cb(cur, total, label='Converting…'):
            pct = int(cur / max(total, 1) * 100) if total != 100 else cur
            _job_set(jid, pct=pct, label=label)

        meshes = _parse_vrml(tmp_path, color_mode, progress_cb=cb)
        _job_set(jid, pct=85, label='Simplifying…')

        import trimesh
        if out_format == 'glb':
            _job_set(jid, pct=88, label='Exporting GLB…')
            combined_for_simplify = trimesh.util.concatenate(meshes)
            if len(combined_for_simplify.faces) > max_faces:
                combined_for_simplify = _simplify(combined_for_simplify, max_faces)
                # rebuild single-mesh list after simplification
                meshes = [combined_for_simplify]
            out_bytes, used_draco = _export_glb(meshes)
        else:
            combined = trimesh.util.concatenate(meshes)
            combined = _simplify(combined, max_faces)
            _job_set(jid, pct=90, label=f'Exporting {out_format.upper()}…')
            raw = combined.export(file_type=out_format)
            out_bytes = bytes(raw) if not isinstance(raw, bytes) else raw
            used_draco = False

        _job_set(jid, pct=100, label='Done!', status='done',
                 result=out_bytes, draco=used_draco,
                 parts=len(meshes), size=len(out_bytes))
        logger.info('Job %s done: %.2f MB  draco=%s', jid, len(out_bytes)/1e6, used_draco)
    except Exception:
        err = traceback.format_exc()
        logger.error('Job %s failed:\n%s', jid, err)
        _job_set(jid, status='error', error='Conversion failed — see server log.')
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
    out_format  = request.form.get('out_format', 'glb').lower()
    if out_format not in ('glb', 'obj', 'stl'):
        out_format = 'glb'
    max_faces   = max(5000, min(int(request.form.get('max_faces', 500000)), 10_000_000))
    color_mode  = request.form.get('color_mode', 'actual')
    if color_mode not in ('actual', 'gray'):
        color_mode = 'actual'

    with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
        tmp_path = Path(tmp.name)
        f.save(tmp)

    jid = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[jid] = {
            'status': 'running', 'pct': 2, 'label': 'Queued…',
            'result': None, 'format': out_format,
            'draco': False, 'parts': 0, 'size': 0, 'error': '',
        }
    t = threading.Thread(target=_run_job,
                         args=(jid, tmp_path, out_format, max_faces, color_mode),
                         daemon=True)
    t.start()
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
                'draco':  job.get('draco', False),
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
    fmt = job.get('format', 'glb')
    mime = {'glb': 'model/gltf-binary', 'obj': 'text/plain',
            'stl': 'application/octet-stream'}.get(fmt, 'application/octet-stream')
    data = job['result']
    # Free memory after serving
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid]['result'] = None
    return send_file(io.BytesIO(data), mimetype=mime,
                     as_attachment=True, download_name=f'model.{fmt}')


if __name__ == '__main__':
    print('\n  VRML / WRL Converter  →  http://localhost:5555\n')
    app.run(host='0.0.0.0', port=5555, debug=False, threaded=True)
