"""VRML 1.0 + 2.0 / WRL -> GLB / OBJ / STL Converter

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

_jobs: dict = {}
_jobs_lock = threading.Lock()

# Check Draco availability once at startup
try:
    import DracoPy
    _DRACO_OK = True
    logger.info('DracoPy available — GLB will use Draco compression')
except ImportError:
    _DRACO_OK = False
    logger.warning('DracoPy not installed — GLB will be uncompressed (pip install DracoPy)')

HTML = """
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
  .row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
  .field { flex: 1; }
  select,input[type=number] { width: 100%; background: #0f172a; border: 1px solid #334155;
    border-radius: 8px; color: #e2e8f0; padding: .55rem .75rem; font-size: .9rem; }
  select:focus,input[type=number]:focus { outline: none; border-color: #7dd3fc; }
  .color-toggle { display: flex; gap: .5rem; margin-bottom: 1.5rem; }
  .color-toggle input[type=radio] { display: none; }
  .color-toggle label.opt { flex: 1; text-align: center; padding: .55rem .5rem;
    border: 1px solid #334155; border-radius: 8px; cursor: pointer;
    font-size: .85rem; color: #94a3b8; transition: all .15s; user-select: none; }
  .color-toggle input[type=radio]:checked + label.opt {
    border-color: #7dd3fc; background: #0f2744; color: #7dd3fc; font-weight: 600; }
  button { width: 100%; background: #0284c7; color: #fff; border: none; border-radius: 10px;
    padding: .85rem; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background .2s; }
  button:hover { background: #0369a1; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  .progress-wrap { margin-top: 1.5rem; display: none; }
  .progress-bar { background: #1e3a5f; border-radius: 8px; height: 12px; overflow: hidden; margin-bottom: .5rem; }
  .progress-fill { height: 100%; background: linear-gradient(90deg,#0284c7,#7dd3fc);
    border-radius: 8px; width: 0%; transition: width .4s ease; }
  .progress-label { font-size: .85rem; color: #94a3b8; text-align: center; min-height: 1.2em; }
  .result { margin-top: 1.5rem; padding: 1rem 1.25rem; border-radius: 10px; font-size: .9rem; display: none; }
  .result.ok  { background: #052e16; border: 1px solid #16a34a; color: #86efac; }
  .result.err { background: #2d0a0a; border: 1px solid #dc2626; color: #fca5a5; }
  .stats { margin-top: .4rem; font-size: .82rem; color: #64748b; }
  .badge { display:inline-block; padding:.15rem .5rem; border-radius:6px;
    font-size:.75rem; font-weight:600; margin-left:.5rem;
    background:#0f2744; color:#7dd3fc; border:1px solid #0284c7; }
</style>
</head>
<body>
<div class="card">
  <h1>&#127922; VRML / WRL Converter
    {% if draco %}<span class="badge">Draco</span>{% endif %}
  </h1>
  <p class="sub">VRML 1.0 &amp; 2.0 + DEF/USE
    {% if draco %}&mdash; Draco GLB compression active{% else %}&mdash; install DracoPy for smaller GLB{% endif %}
  </p>

  <label>1. Choose your WRL / VRML file</label>
  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <input type="file" id="fileInput" accept=".wrl,.vrml" onchange="onFileChosen(this)"/>
    <div class="icon">&#128196;</div>
    <div>Click to browse or drag &amp; drop</div>
    <div class="hint">.wrl / .vrml &mdash; up to 4 GB</div>
    <div class="chosen" id="chosenName"></div>
  </div>

  <div class="row">
    <div class="field">
      <label>2. Output format</label>
      <select id="outFmt">
        <option value="glb">GLB{% if draco %} (Draco){% endif %}</option>
        <option value="obj">OBJ</option>
        <option value="stl">STL</option>
      </select>
    </div>
    <div class="field">
      <label>3. Max faces</label>
      <input type="number" id="maxFaces" value="500000" min="5000" max="10000000" step="50000"/>
    </div>
  </div>

  <label>4. Color mode</label>
  <div class="color-toggle">
    <input type="radio" name="colorMode" id="cmActual" value="actual" checked/>
    <label class="opt" for="cmActual">&#127752; Actual Colors</label>
    <input type="radio" name="colorMode" id="cmGray" value="gray"/>
    <label class="opt" for="cmGray">&#9643; Light Gray</label>
  </div>

  <button id="convertBtn" onclick="startConvert()" disabled>Convert</button>

  <div class="progress-wrap" id="progressWrap">
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-label" id="progressLabel">Starting...</div>
  </div>
  <div class="result" id="resultBox"></div>
</div>

<script>
let chosenFile = null;
const dz = document.getElementById('dropZone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('dragover'); const f = e.dataTransfer.files[0]; if (f) setFile(f); });
function onFileChosen(i) { if (i.files[0]) setFile(i.files[0]); }
function setFile(f) {
  chosenFile = f;
  document.getElementById('chosenName').textContent = f.name + '  (' + fmt(f.size) + ')';
  document.getElementById('convertBtn').disabled = false;
}
function fmt(b) {
  if (b > 1e9) return (b/1e9).toFixed(1)+' GB';
  if (b > 1e6) return (b/1e6).toFixed(1)+' MB';
  return (b/1e3).toFixed(0)+' KB';
}
async function startConvert() {
  if (!chosenFile) return;
  const btn=document.getElementById('convertBtn'),pw=document.getElementById('progressWrap'),
        pf=document.getElementById('progressFill'),pl=document.getElementById('progressLabel'),
        rb=document.getElementById('resultBox');
  btn.disabled=true; rb.style.display='none'; pw.style.display='block';
  pf.style.width='2%'; pl.textContent='Uploading...';

  const form=new FormData();
  form.append('file',chosenFile);
  form.append('out_format',document.getElementById('outFmt').value);
  form.append('max_faces',document.getElementById('maxFaces').value);
  const cm=document.querySelector('input[name=colorMode]:checked');
  form.append('color_mode', cm ? cm.value : 'actual');

  let jobId;
  try {
    const xhr=new XMLHttpRequest(); xhr.open('POST','/start');
    const done=new Promise((res,rej)=>{
      xhr.upload.onprogress=e=>{ if(e.lengthComputable){const p=Math.round(e.loaded/e.total*30); pf.style.width=p+'%'; pl.textContent='Uploading... '+p+'%';} };
      xhr.onload=()=>xhr.status===200?res(JSON.parse(xhr.responseText)):rej(new Error(JSON.parse(xhr.responseText).detail||'Upload failed'));
      xhr.onerror=()=>rej(new Error('Network error'));
    });
    xhr.send(form);
    jobId=(await done).job_id;
  } catch(err){ showError(err.message); btn.disabled=false; return; }

  pf.style.width='32%'; pl.textContent='Processing...';

  const es=new EventSource('/progress/'+jobId);
  es.onmessage=e=>{
    const d=JSON.parse(e.data);
    pf.style.width=(30+Math.round(d.pct*0.70))+'%';
    pl.textContent=d.label||'Processing...';
    if(d.status==='done'){
      es.close(); pf.style.width='100%'; pl.textContent='Done!';
      const a=document.createElement('a'),base=chosenFile.name.replace(/\\.[^.]+$/,'');
      a.href='/download/'+jobId;
      a.download=base+'.'+document.getElementById('outFmt').value;
      a.click();
      rb.className='result ok'; rb.style.display='block';
      rb.innerHTML='&#9989; Done! <div class="stats">'
        +fmt(d.size)+' &mdash; '+d.parts+' parts &rarr; '+d.merged+' color groups'
        +(d.draco?' &mdash; <strong>Draco compressed</strong>':'')+'</div>';
      btn.disabled=false;
    } else if(d.status==='error'){ es.close(); showError(d.error||'Conversion failed'); btn.disabled=false; }
  };
  es.onerror=()=>{ es.close(); showError('Lost connection.'); btn.disabled=false; };
}
function showError(msg){
  const rb=document.getElementById('resultBox');
  rb.className='result err'; rb.style.display='block'; rb.textContent='Error: '+msg;
  document.getElementById('progressWrap').style.display='none';
}
</script>
</body></html>
"""


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_FLT         = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
_NUM_RE      = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_BRACE_RE    = re.compile(r'[{}]')
_BRACKET_RE  = re.compile(r'[\[\]]')

_DIRECT_RE = re.compile(
    r'\b(MatrixTransform|Transform|Separator|Group|LOD|Switch'
    r'|TransformSeparator|Coordinate3|Coordinate|IndexedFaceSet|IndexedLineSet'
    r'|Shape|Appearance|Anchor|Billboard|Collision'
    r'|Material|MaterialBinding|Normal|NormalBinding|ShapeHints'
    r'|Info|Texture2|Texture2Transform|TextureCoordinate2'
    r'|PointLight|DirectionalLight|SpotLight|OrthographicCamera|PerspectiveCamera'
    r'|Cube|Sphere|Cone|Cylinder|AsciiText'
    r'|FontStyle|DrawStyle|LightModel|BaseColor|PackedColor'
    r'|EnvironmentMap|ShapeKit|WWWAnchor|WWWInline'
    r')\s*\{'
)

_DEF_RE            = re.compile(r'\bDEF\s+(\w+)\s+(\w+)\s*\{')
_STANDALONE_USE_RE = re.compile(r'(?<![\w.])USE\s+(\w+)')
_FIELD_USE_RE      = re.compile(
    r'\b(?:coord|appearance|geometry|material|normal|color|texCoord|children'
    r'|proxy|level|range|choice|whichChoice)\s+USE\s+(\w+)')

_PT_RE          = re.compile(r'\bpoint\s*\[')
_CI_RE          = re.compile(r'\bcoordIndex\s*\[')
_DC_RE          = re.compile(rf'diffuseColor\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_FLT16          = r'\s+'.join([rf'({_FLT})'] * 16)
_MTX_VALS_RE    = re.compile(r'\bmatrix\s+' + _FLT16)
_TR1_RE         = re.compile(rf'\btranslation\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_SC1_RE         = re.compile(rf'\bscaleFactor\s+({_FLT})(?:\s+({_FLT})\s+({_FLT}))?')
_RO1_RE         = re.compile(rf'\brotation\s+({_FLT})\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_COORD_FIELD_RE = re.compile(r'\bcoord\s+(?:DEF\s+\w+\s+)?Coordinate\s*\{')
_COORD_USE_RE   = re.compile(r'\bcoord\s+USE\s+(\w+)')


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def _build_brace_index(text):
    idx, stack = {}, []
    for m in _BRACE_RE.finditer(text):
        if m.group() == '{': stack.append(m.start())
        elif stack: idx[stack.pop()] = m.start()
    return idx

def _build_bracket_index(text):
    idx, stack = {}, []
    for m in _BRACKET_RE.finditer(text):
        if m.group() == '[': stack.append(m.start())
        elif stack: idx[stack.pop()] = m.start()
    return idx

def _build_def_map(text, brace_idx):
    def_map = {}
    for m in _DEF_RE.finditer(text):
        name, node_type = m.group(1), m.group(2)
        bo = text.find('{', m.start())
        if bo == -1: continue
        bc = brace_idx.get(bo)
        if bc is None: continue
        def_map[name] = (node_type, bo + 1, bc)
    return def_map

def _build_global_field_uses(text):
    positions = set()
    for m in _FIELD_USE_RE.finditer(text):
        pos = text.index('USE', m.start())
        positions.add(pos)
    return positions


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def _bracket_pos(text, start, end, bracket_idx):
    p = text.find('[', start)
    if p == -1 or p >= end: return None
    cl = bracket_idx.get(p)
    return (p + 1, cl) if (cl is not None and cl <= end) else None


# ---------------------------------------------------------------------------
# Direct-child scanner
# ---------------------------------------------------------------------------

def _direct_children(text, cs, ce, brace_idx, def_map, field_uses):
    pos = cs
    while pos < ce:
        dm = _DIRECT_RE.search(text, pos, ce)
        um = _STANDALONE_USE_RE.search(text, pos, ce)
        while um is not None and um.start() in field_uses:
            um = _STANDALONE_USE_RE.search(text, um.end(), ce)
        if dm is None and um is None: break
        use_first = (um is not None) and (dm is None or um.start() < dm.start())
        if use_first:
            name = um.group(1)
            entry = def_map.get(name)
            if entry:
                node_type, dcs, dce = entry
                yield node_type, um.start(), dcs, dce
            pos = um.end()
        else:
            node = dm.group(1)
            brace_open = text.find('{', dm.start())
            if brace_open == -1 or brace_open >= ce: pos = dm.end(); continue
            brace_close = brace_idx.get(brace_open)
            if brace_close is None or brace_close > ce: pos = dm.end(); continue
            yield node, dm.start(), brace_open + 1, brace_close
            pos = brace_close + 1


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _axis_angle_to_mat4(x, y, z, angle):
    L = np.sqrt(x*x + y*y + z*z)
    if L < 1e-10: return np.eye(4)
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


# ---------------------------------------------------------------------------
# Face builder
# ---------------------------------------------------------------------------

def _build_faces(indices):
    faces, fan = [], []
    for idx in indices:
        if idx < 0:
            if len(fan) >= 3:
                for j in range(1, len(fan) - 1):
                    faces.append((fan[0], fan[j], fan[j+1]))
            fan = []
        else:
            fan.append(idx)
    if len(fan) >= 3:
        for j in range(1, len(fan) - 1):
            faces.append((fan[0], fan[j], fan[j+1]))
    return np.array(faces, dtype=np.int64) if faces else np.empty((0,3), dtype=np.int64)


# ---------------------------------------------------------------------------
# Coord / color extraction
# ---------------------------------------------------------------------------

def _extract_points(text, ncs, nce, bracket_idx):
    pt_m = _PT_RE.search(text, ncs, nce)
    if not pt_m: return None
    pp = _bracket_pos(text, pt_m.start(), nce, bracket_idx)
    if not pp: return None
    floats = _parse_floats(text, pp)
    if floats is None or len(floats) < 9 or len(floats) % 3 != 0: return None
    return floats.reshape(-1, 3)

def _extract_diffuse(text, ncs, nce):
    hdr = text[ncs: min(ncs+512, nce)]
    dc = _DC_RE.search(hdr)
    if not dc: return None
    return [int(float(dc.group(i))*255) for i in (1,2,3)] + [255]

def _inline_coord(text, ncs, nce, brace_idx, bracket_idx, def_map):
    cm = _COORD_FIELD_RE.search(text, ncs, nce)
    if cm:
        bo = text.find('{', cm.start())
        if bo != -1 and bo < nce:
            bc = brace_idx.get(bo)
            if bc is not None and bc <= nce:
                r = _extract_points(text, bo+1, bc, bracket_idx)
                if r is not None: return r
    um = _COORD_USE_RE.search(text, ncs, nce)
    if um:
        entry = def_map.get(um.group(1))
        if entry:
            _, dcs, dce = entry
            r = _extract_points(text, dcs, dce, bracket_idx)
            if r is not None: return r
    return None


# ---------------------------------------------------------------------------
# VRML walker
# ---------------------------------------------------------------------------

_CONTAINERS = frozenset({
    'Separator','Group','Switch','TransformSeparator',
    'Shape','Anchor','Billboard','Collision',
})

def _walk(text, meshes, brace_idx, bracket_idx, def_map, field_uses, progress_cb=None):
    from trimesh.visual.material import PBRMaterial
    import trimesh

    _DEFAULT_COLOR = [210, 210, 210, 255]
    stack = [(0, len(text), np.eye(4), None, list(_DEFAULT_COLOR))]
    total_tried = 0
    file_len = len(text)

    while stack:
        cs, ce, parent_matrix, parent_coord, parent_color = stack.pop()
        current_matrix = np.copy(parent_matrix)
        current_coord  = parent_coord
        current_color  = list(parent_color)

        for node, node_pos, ncs, nce in _direct_children(
                text, cs, ce, brace_idx, def_map, field_uses):

            if node == 'MatrixTransform':
                vm = _MTX_VALS_RE.search(text, ncs, nce)
                if vm:
                    mat = np.array([float(vm.group(i)) for i in range(1,17)],
                                   dtype=np.float64).reshape(4,4)
                    current_matrix = parent_matrix @ mat
                stack.append((ncs, nce, current_matrix, current_coord, list(current_color)))

            elif node == 'Transform':
                hdr = text[ncs: min(ncs+800, nce)]
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
                    sx=float(sc.group(1)); sy=float(sc.group(2)) if sc.group(2) else sx
                    sz=float(sc.group(3)) if sc.group(3) else sx
                    M = M @ np.diag([sx, sy, sz, 1.0])
                current_matrix = parent_matrix @ M
                stack.append((ncs, nce, current_matrix, current_coord, list(current_color)))

            elif node in ('Coordinate3','Coordinate'):
                coords = _extract_points(text, ncs, nce, bracket_idx)
                if coords is not None: current_coord = coords

            elif node == 'Material':
                col = _extract_diffuse(text, ncs, nce)
                if col: current_color = col

            elif node == 'Appearance':
                for child, _, ccs, cce in _direct_children(
                        text, ncs, nce, brace_idx, def_map, field_uses):
                    if child == 'Material':
                        col = _extract_diffuse(text, ccs, cce)
                        if col: current_color = col
                        break

            elif node in _CONTAINERS:
                stack.append((ncs, nce, current_matrix, current_coord, list(current_color)))

            elif node == 'LOD':
                for child_node, _, ccs, cce in _direct_children(
                        text, ncs, nce, brace_idx, def_map, field_uses):
                    if child_node in _CONTAINERS or child_node in ('LOD','Transform','MatrixTransform'):
                        stack.append((ccs, cce, current_matrix, current_coord, list(current_color)))
                        break

            elif node == 'IndexedFaceSet':
                total_tried += 1
                coord_to_use = (_inline_coord(text, ncs, nce, brace_idx, bracket_idx, def_map)
                                or current_coord)
                if coord_to_use is None: continue
                ci_m = _CI_RE.search(text, ncs, nce)
                if ci_m is None: continue
                ci_pp = _bracket_pos(text, ci_m.start(), nce, bracket_idx)
                if ci_pp is None: continue
                indices = [int(x) for x in re.findall(r'-?\d+', text[ci_pp[0]:ci_pp[1]])]
                if not indices: continue
                faces = _build_faces(indices)
                if len(faces) == 0: continue
                valid = np.all((faces >= 0) & (faces < len(coord_to_use)), axis=1)
                faces = faces[valid]
                if len(faces) == 0: continue

                mesh = trimesh.Trimesh(vertices=coord_to_use.copy(), faces=faces, process=False)
                if not np.allclose(current_matrix, np.eye(4)):
                    mesh.apply_transform(current_matrix)

                r, g, b, a = current_color
                from trimesh.visual.material import PBRMaterial
                pbr = PBRMaterial(
                    baseColorFactor=np.array([r/255.0, g/255.0, b/255.0, a/255.0]),
                    metallicFactor=0.0, roughnessFactor=0.7,
                )
                mesh.visual = trimesh.visual.TextureVisuals(material=pbr)
                meshes.append(mesh)

                if progress_cb and len(meshes) % 50 == 0:
                    pct = int(20 + min(ncs/file_len, 1.0)*55)
                    progress_cb(pct, f'Parsing... {len(meshes)} parts found')

    logger.info('IFS tried: %d  succeeded: %d', total_tried, len(meshes))


# ---------------------------------------------------------------------------
# Post-process
# ---------------------------------------------------------------------------

def _apply_gray(meshes):
    import trimesh
    from trimesh.visual.material import PBRMaterial
    gray = np.array([0.82, 0.82, 0.82, 1.0])
    for m in meshes:
        pbr = PBRMaterial(baseColorFactor=gray, metallicFactor=0.0, roughnessFactor=0.6)
        m.visual = trimesh.visual.TextureVisuals(material=pbr)
    return meshes

def _merge_by_color(meshes):
    import trimesh
    groups: dict = {}
    for m in meshes:
        try:
            key = tuple(round(float(x), 2) for x in m.visual.material.baseColorFactor)
        except Exception:
            key = ('default',)
        groups.setdefault(key, []).append(m)
    result = []
    for key, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
        else:
            merged = trimesh.util.concatenate(group)
            merged.visual = group[0].visual
            result.append(merged)
    logger.info('Merged %d parts -> %d color groups', len(meshes), len(result))
    return result

def _simplify_list(meshes, max_faces):
    total = sum(len(m.faces) for m in meshes)
    if total <= max_faces: return meshes
    ratio = max_faces / total
    out = []
    for m in meshes:
        target = max(4, int(len(m.faces)*ratio))
        simplified = m
        for method in ('simplify_quadric_decimation','simplify_quadratic_decimation'):
            if hasattr(m, method):
                try:
                    simplified = getattr(m, method)(target)
                    simplified.visual = m.visual
                except Exception: pass
                break
        out.append(simplified)
    logger.info('Simplify: %d -> %d faces', total, sum(len(m.faces) for m in out))
    return out


# ---------------------------------------------------------------------------
# GLB export (standard + Draco)
# ---------------------------------------------------------------------------

def _glb_pack(json_dict, bin_buf):
    """Pack JSON + binary buffer into a GLB binary."""
    json_bytes = json.dumps(json_dict, separators=(',',':')).encode('utf-8')
    # 4-byte align JSON
    while len(json_bytes) % 4:
        json_bytes += b' '
    # 4-byte align BIN
    while len(bin_buf) % 4:
        bin_buf.append(0)
    bin_bytes = bytes(bin_buf)
    total = 12 + 8 + len(json_bytes) + (8 + len(bin_bytes) if bin_bytes else 0)
    out = struct.pack('<III', 0x46546C67, 2, total)
    out += struct.pack('<II', len(json_bytes), 0x4E4F534A) + json_bytes
    if bin_bytes:
        out += struct.pack('<II', len(bin_bytes), 0x004E4942) + bin_bytes
    return out


def _export_glb_draco(meshes):
    """Build GLB with KHR_draco_mesh_compression."""
    import DracoPy

    gltf = {
        "asset": {"version": "2.0", "generator": "vrml-converter"},
        "extensionsUsed": ["KHR_draco_mesh_compression"],
        "extensionsRequired": ["KHR_draco_mesh_compression"],
        "scene": 0,
        "scenes": [{"nodes": list(range(len(meshes)))}],
        "nodes": [{"mesh": i} for i in range(len(meshes))],
        "meshes": [], "accessors": [], "bufferViews": [],
        "buffers": [{"byteLength": 0}], "materials": [],
    }
    bin_buf = bytearray()

    for mesh in meshes:
        verts = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.faces,    dtype=np.uint32)

        # Draco encode — try numpy API first, fall back to list API
        try:
            draco_bytes = DracoPy.encode(verts, faces)
        except Exception:
            draco_bytes = DracoPy.encode_mesh_to_buffer(
                verts.flatten().tolist(), faces.flatten().tolist())

        # 4-byte align
        while len(bin_buf) % 4:
            bin_buf.append(0)
        bv_offset = len(bin_buf)
        bin_buf.extend(draco_bytes)

        bv_idx = len(gltf["bufferViews"])
        gltf["bufferViews"].append({
            "buffer": 0, "byteOffset": bv_offset, "byteLength": len(draco_bytes)
        })

        # Accessors (no bufferView — data comes from Draco)
        pos_acc = len(gltf["accessors"])
        gltf["accessors"].append({
            "componentType": 5126, "count": len(verts), "type": "VEC3",
            "min": verts.min(0).tolist(), "max": verts.max(0).tolist(),
        })
        idx_acc = len(gltf["accessors"])
        gltf["accessors"].append({
            "componentType": 5125, "count": int(faces.size), "type": "SCALAR",
        })

        # Material
        try:
            color = [float(c) for c in mesh.visual.material.baseColorFactor]
        except Exception:
            color = [0.82, 0.82, 0.82, 1.0]
        mat_idx = len(gltf["materials"])
        gltf["materials"].append({
            "pbrMetallicRoughness": {
                "baseColorFactor": color,
                "metallicFactor": 0.0, "roughnessFactor": 0.7,
            }
        })

        gltf["meshes"].append({"primitives": [{
            "attributes": {"POSITION": pos_acc},
            "indices": idx_acc,
            "material": mat_idx,
            "extensions": {
                "KHR_draco_mesh_compression": {
                    "bufferView": bv_idx,
                    "attributes": {"POSITION": 0},
                }
            }
        }]})

    gltf["buffers"][0]["byteLength"] = len(bin_buf)
    return _glb_pack(gltf, bin_buf)


def _export_glb_standard(meshes):
    """Standard uncompressed GLB via trimesh Scene."""
    import trimesh
    scene = trimesh.Scene()
    for i, m in enumerate(meshes):
        scene.add_geometry(m, node_name=f'part_{i}')
    return scene.export(file_type='glb')


def _export_glb(meshes):
    if _DRACO_OK:
        try:
            result = _export_glb_draco(meshes)
            logger.info('Draco GLB: %.2f MB', len(result)/1e6)
            return result
        except Exception as e:
            logger.warning('Draco export failed (%s), falling back to standard GLB', e)
    return _export_glb_standard(meshes)


def _export_flat(meshes, file_type):
    import trimesh
    combined = trimesh.util.concatenate(meshes)
    return combined.export(file_type=file_type)

def _to_bytes(out):
    return out.encode('utf-8') if isinstance(out, str) else bytes(out)


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------

def _set_progress(job_id, pct, label=''):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]['pct'] = pct
            _jobs[job_id]['label'] = label

def _run_job(job_id, src_path, out_format, max_faces, color_mode, stem):
    def prog(pct, label): _set_progress(job_id, pct, label)
    try:
        prog(5,  'Reading file...')
        text = src_path.read_text(encoding='utf-8', errors='replace')
        prog(8,  'Building brace index...')
        brace_idx = _build_brace_index(text)
        prog(11, 'Building bracket index...')
        bracket_idx = _build_bracket_index(text)
        prog(13, 'Scanning DEF map...')
        def_map = _build_def_map(text, brace_idx)
        prog(15, 'Pre-computing field references...')
        field_uses = _build_global_field_uses(text)
        prog(18, 'Parsing geometry...')
        meshes = []
        _walk(text, meshes, brace_idx, bracket_idx, def_map, field_uses, progress_cb=prog)
        del text

        if not meshes:
            raise ValueError('No geometry found')

        n_parts = len(meshes)
        prog(76, f'Found {n_parts} parts, applying color...')
        if color_mode == 'gray':
            meshes = _apply_gray(meshes)

        prog(80, 'Simplifying geometry...')
        meshes = _simplify_list(meshes, max_faces)

        prog(86, 'Merging same-color groups...')
        meshes = _merge_by_color(meshes)
        n_merged = len(meshes)

        prog(91, f'Exporting {n_merged} groups{" (Draco)" if (_DRACO_OK and out_format=="glb") else ""}...')
        raw = _export_glb(meshes) if out_format == 'glb' else _export_flat(meshes, out_format)
        out_bytes = _to_bytes(raw)
        del meshes

        result_path = src_path.with_suffix(f'.out.{out_format}')
        result_path.write_bytes(out_bytes)
        size = len(out_bytes)
        del out_bytes

        logger.info('Job %s done: %.2f MB', job_id, size/1e6)
        with _jobs_lock:
            _jobs[job_id].update({
                'status':'done','pct':100,'label':'Done!',
                'result_path':result_path,'result_size':size,
                'stem':stem,'out_format':out_format,
                'n_parts':n_parts,'n_merged':n_merged,
                'draco':_DRACO_OK and out_format=='glb',
            })
    except Exception:
        err = traceback.format_exc()
        logger.error(err)
        short = err.strip().splitlines()[-1]
        with _jobs_lock:
            _jobs[job_id].update({'status':'error','error':short,'pct':0,'label':'Error'})
    finally:
        if src_path.exists():
            try: src_path.unlink()
            except Exception: pass


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template_string(HTML, draco=_DRACO_OK)

@app.route('/start', methods=['POST'])
def start():
    f = request.files.get('file')
    if not f or not f.filename: return jsonify(detail='No file received.'), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('wrl','vrml'): return jsonify(detail=f'Only .wrl/.vrml supported.'), 400
    out_format = request.form.get('out_format','glb').lower()
    if out_format not in ('glb','obj','stl'): out_format = 'glb'
    max_faces  = max(5000, min(int(request.form.get('max_faces', 500000)), 10_000_000))
    color_mode = request.form.get('color_mode','actual')
    stem       = Path(f.filename).stem
    tmp = tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False)
    f.save(tmp); tmp.close()
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {'status':'running','pct':0,'label':'Starting...'}
    t = threading.Thread(target=_run_job,
        args=(job_id, Path(tmp.name), out_format, max_faces, color_mode, stem),
        daemon=True)
    t.start()
    return jsonify(job_id=job_id)

@app.route('/progress/<job_id>')
def progress(job_id):
    def generate():
        while True:
            with _jobs_lock: job = _jobs.get(job_id)
            if job is None:
                yield f'data: {json.dumps({"status":"error","error":"Job not found"})}\n\n'
                return
            payload = {'pct':job.get('pct',0),'label':job.get('label',''),'status':job.get('status','running')}
            if job.get('status') == 'done':
                payload.update({'size':job.get('result_size',0),'parts':job.get('n_parts',''),
                                'merged':job.get('n_merged',''),'draco':job.get('draco',False)})
            elif job.get('status') == 'error':
                payload['error'] = job.get('error','Unknown error')
            yield f'data: {json.dumps(payload)}\n\n'
            if job.get('status') in ('done','error'): return
            time.sleep(0.5)
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.route('/download/<job_id>')
def download(job_id):
    with _jobs_lock: job = _jobs.get(job_id)
    if not job or job.get('status') != 'done':
        return jsonify(detail='Not ready.'), 404
    mime = {'glb':'model/gltf-binary','obj':'text/plain','stl':'application/octet-stream'}[job['out_format']]
    return send_file(job['result_path'], mimetype=mime, as_attachment=True,
                     download_name=f"{job['stem']}.{job['out_format']}")

if __name__ == '__main__':
    print('\n  VRML / WRL Converter  ->  http://localhost:5555\n')
    app.run(host='0.0.0.0', port=5555, debug=False, threaded=True)
