"""VRML / WRL → GLB / OBJ / STL Converter  (DEF/USE, LOD, Collision, bare USE)
Usage:
    pip install flask trimesh[easy] numpy
    python app.py
Open http://localhost:5555
"""

import io
import logging
import re
import tempfile
import traceback
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, render_template_string, request, send_file

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024

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
    width: 100%; max-width: 560px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
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
  button { width: 100%; background: #0284c7; color: #fff; border: none; border-radius: 10px;
    padding: .85rem; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background .2s; }
  button:hover { background: #0369a1; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  .progress-wrap { margin-top: 1.5rem; display: none; }
  .progress-bar { background: #1e3a5f; border-radius: 8px; height: 10px; overflow: hidden; margin-bottom: .5rem; }
  .progress-fill { height: 100%; background: linear-gradient(90deg,#0284c7,#7dd3fc);
    border-radius: 8px; width: 0%; transition: width .3s ease; }
  .progress-label { font-size: .85rem; color: #94a3b8; text-align: center; }
  .result { margin-top: 1.5rem; padding: 1rem 1.25rem; border-radius: 10px; font-size: .9rem; display: none; }
  .result.ok  { background: #052e16; border: 1px solid #16a34a; color: #86efac; }
  .result.err { background: #2d0a0a; border: 1px solid #dc2626; color: #fca5a5; }
  .stats { margin-top: .6rem; font-size: .82rem; color: #64748b; }
</style>
</head>
<body>
<div class="card">
  <h1>&#127922; VRML / WRL Converter</h1>
  <p class="sub">Runs locally on CPU &mdash; no GPU needed</p>
  <label>1. Choose your WRL / VRML file</label>
  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <input type="file" id="fileInput" accept=".wrl,.vrml" onchange="onFileChosen(this)"/>
    <div class="icon">&#128196;</div>
    <div>Click to browse or drag &amp; drop</div>
    <div class="hint">Supports .wrl &amp; .vrml</div>
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
      <input type="number" id="maxFaces" value="200000" min="5000" max="5000000" step="10000"/>
    </div>
  </div>
  <button id="convertBtn" onclick="convert()" disabled>Convert</button>
  <div class="progress-wrap" id="progressWrap">
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-label" id="progressLabel">Uploading…</div>
  </div>
  <div class="result" id="resultBox"></div>
</div>
<script>
let chosenFile=null;
const dz=document.getElementById('dropZone');
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('dragover');});
dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));
dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('dragover');const f=e.dataTransfer.files[0];if(f)setFile(f);});
function onFileChosen(i){if(i.files[0])setFile(i.files[0]);}
function setFile(f){chosenFile=f;document.getElementById('chosenName').textContent=f.name+'  ('+fmt(f.size)+')';document.getElementById('convertBtn').disabled=false;}
function fmt(b){if(b>1e9)return(b/1e9).toFixed(1)+' GB';if(b>1e6)return(b/1e6).toFixed(1)+' MB';return(b/1e3).toFixed(0)+' KB';}
async function convert(){
  if(!chosenFile)return;
  const btn=document.getElementById('convertBtn'),pw=document.getElementById('progressWrap'),
    pf=document.getElementById('progressFill'),pl=document.getElementById('progressLabel'),rb=document.getElementById('resultBox');
  btn.disabled=true;rb.style.display='none';pw.style.display='block';pf.style.width='5%';pl.textContent='Uploading…';
  const form=new FormData();
  form.append('file',chosenFile);
  form.append('out_format',document.getElementById('outFmt').value);
  form.append('max_faces',document.getElementById('maxFaces').value);
  const xhr=new XMLHttpRequest();xhr.open('POST','/convert');xhr.responseType='blob';
  xhr.upload.onprogress=e=>{if(e.lengthComputable){const p=Math.round(e.loaded/e.total*50);pf.style.width=p+'%';pl.textContent='Uploading… '+p+'%';}};
  let fp=50;const tk=setInterval(()=>{fp=Math.min(fp+(fp<70?2:fp<88?.8:.2),94);pf.style.width=fp+'%';pl.textContent='Converting… '+Math.round(fp)+'%';},600);
  xhr.onload=()=>{
    clearInterval(tk);pf.style.width='100%';pl.textContent='Done!';btn.disabled=false;
    if(xhr.status===200){
      const url=URL.createObjectURL(xhr.response),base=chosenFile.name.replace(/\\.[^.]+$/,''),a=document.createElement('a');
      a.href=url;a.download=base+'.'+document.getElementById('outFmt').value;a.click();URL.revokeObjectURL(url);
      rb.className='result ok';rb.style.display='block';
      rb.innerHTML='&#9989; Done! <span class="stats">'+fmt(xhr.response.size)+'</span>';
    }else{xhr.response.text().then(t=>{let m='Conversion failed.';try{m=JSON.parse(t).detail||m;}catch(_){}rb.className='result err';rb.style.display='block';rb.textContent='✗ '+m;});}
  };
  xhr.onerror=()=>{clearInterval(tk);btn.disabled=false;rb.className='result err';rb.style.display='block';rb.textContent='✗ Network error.';};
  xhr.send(form);
}
</script>
</body></html>
"""


# ── Compiled patterns ─────────────────────────────────────────────────────────────────────

_FLT        = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'

# Container nodes we descend into (added LOD, Collision, Billboard, Anchor, Inline-like)
_CONTAINER  = frozenset({
    'Transform','Group','Separator','Switch',
    'LOD','Collision','Billboard','Anchor','Detail',
})
_SHAPE_LIKE = frozenset({'Shape','IndexedFaceSet'})

_NODE_RE    = re.compile(
    r'\b(Transform|Group|Separator|Switch|LOD|Collision|Billboard|Anchor|Detail'
    r'|Shape|IndexedFaceSet)\s*\{'
)
# bare USE at statement level:  USE SomeName  (not inside a field value)
_STMT_USE_RE = re.compile(r'(?:^|\n)\s*USE\s+(\S+)')
_IFS_RE     = re.compile(r'\bIndexedFaceSet\s*\{')
_CI_RE      = re.compile(r'\bcoordIndex\s*\[')
_PT_RE      = re.compile(r'\bpoint\s*\[')
_NUM_RE     = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_TR_RE      = re.compile(rf'\btranslation\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_SC_RE      = re.compile(rf'\bscale\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_RO_RE      = re.compile(rf'\brotation\s+({_FLT})\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_DC_RE      = re.compile(rf'diffuseColor\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_TP_RE      = re.compile(rf'transparency\s+({_FLT})')
_BRACE_RE   = re.compile(r'[{}]')
_BRACKET_RE = re.compile(r'[\[\]]')
_DEF_RE     = re.compile(r'\bDEF\s+(\S+)\s+(\w+)\s*\{')
_USE_RE     = re.compile(r'\bUSE\s+(\S+)')
_COORD_RE   = re.compile(r'\bcoord\b')


# ── Brace / bracket index ────────────────────────────────────────────────────────────────

def _build_brace_index(text):
    logger.info('Building brace index …')
    index = {}
    stack = []
    for m in _BRACE_RE.finditer(text):
        if m.group() == '{': stack.append(m.start())
        elif stack:           index[stack.pop()] = m.start()
    logger.info('Brace index: %d pairs', len(index))
    return index


def _build_bracket_index(text):
    index = {}
    stack = []
    for m in _BRACKET_RE.finditer(text):
        if m.group() == '[': stack.append(m.start())
        elif stack:          index[stack.pop()] = m.start()
    return index


# ── DEF registry ─────────────────────────────────────────────────────────────────────────

def _build_def_registry(text, brace_idx):
    """
    {name: (node_type, content_start, content_end)}
    Covers ALL DEF nodes so we can resolve any USE reference.
    """
    logger.info('Building DEF registry …')
    registry = {}
    for m in _DEF_RE.finditer(text):
        name      = m.group(1)
        node_type = m.group(2)
        brace_start = text.find('{', m.start())
        if brace_start == -1: continue
        close = brace_idx.get(brace_start)
        if close is None: continue
        registry[name] = (node_type, brace_start + 1, close)
    logger.info('DEF registry: %d named nodes', len(registry))
    return registry


# ── Transform math ────────────────────────────────────────────────────────────────────────

def _axis_angle_matrix(x, y, z, angle):
    L = np.sqrt(x*x + y*y + z*z)
    if L < 1e-10: return np.eye(4)
    x, y, z = x/L, y/L, z/L
    c, s, t = np.cos(angle), np.sin(angle), 1-np.cos(angle)
    m = np.eye(4)
    m[0,0]=t*x*x+c;   m[0,1]=t*x*y-s*z; m[0,2]=t*x*z+s*y
    m[1,0]=t*x*y+s*z; m[1,1]=t*y*y+c;   m[1,2]=t*y*z-s*x
    m[2,0]=t*x*z-s*y; m[2,1]=t*y*z+s*x; m[2,2]=t*z*z+c
    return m


def _transform_matrix(text, cs, ce):
    hdr = text[cs: min(cs+2000, ce)]
    M = np.eye(4)
    tr = _TR_RE.search(hdr)
    if tr: M[0,3]=float(tr.group(1)); M[1,3]=float(tr.group(2)); M[2,3]=float(tr.group(3))
    S = np.eye(4)
    sc = _SC_RE.search(hdr)
    if sc: S[0,0]=float(sc.group(1)); S[1,1]=float(sc.group(2)); S[2,2]=float(sc.group(3))
    R = np.eye(4)
    ro = _RO_RE.search(hdr)
    if ro: R = _axis_angle_matrix(float(ro.group(1)),float(ro.group(2)),
                                   float(ro.group(3)),float(ro.group(4)))
    return M @ R @ S


# ── Position helpers ─────────────────────────────────────────────────────────────────────

def _brace_pos(text, start, brace_idx):
    p = text.find('{', start)
    if p == -1: return None
    close = brace_idx.get(p)
    if close is None: return None
    return (p+1, close)


def _bracket_pos(text, start, end, bracket_idx):
    p = text.find('[', start)
    if p == -1 or p >= end: return None
    close = bracket_idx.get(p)
    if close is None or close > end: return None
    return (p+1, close)


# ── Point extraction ──────────────────────────────────────────────────────────────────────

def _get_points(text, cs, ce, brace_idx, bracket_idx, def_registry):
    # 1. inline point inside block
    pt_m = _PT_RE.search(text, cs, ce)
    if pt_m:
        pt_pos = _bracket_pos(text, pt_m.start(), ce, bracket_idx)
        if pt_pos: return _parse_floats(text, pt_pos)

    # 2. coord USE <name>
    coord_m = _COORD_RE.search(text, cs, ce)
    if coord_m:
        use_m = _USE_RE.search(text, coord_m.start(), min(coord_m.start()+100, ce))
        if use_m:
            name = use_m.group(1)
            if name in def_registry:
                _, dcs, dce = def_registry[name]
                pt_m2 = _PT_RE.search(text, dcs, dce)
                if pt_m2:
                    pt_pos2 = _bracket_pos(text, pt_m2.start(), dce, bracket_idx)
                    if pt_pos2: return _parse_floats(text, pt_pos2)

    # 3. backwards search up to 200k
    ws = max(0, cs - 200000)
    last = None
    for lm in _PT_RE.finditer(text, ws, cs):
        last = lm
    if last:
        pt_pos3 = _bracket_pos(text, last.start(), last.start() + 200000, bracket_idx)
        if pt_pos3: return _parse_floats(text, pt_pos3)

    return None


def _parse_floats(text, pos_tuple):
    nums = _NUM_RE.findall(text[pos_tuple[0]:pos_tuple[1]])
    if not nums: return None
    return np.array(nums, dtype=np.float64)


# ── Geometry extraction ──────────────────────────────────────────────────────────────

def _build_faces(indices):
    faces, fan = [], []
    for idx in indices:
        if idx < 0:
            if len(fan) >= 3:
                for j in range(1, len(fan)-1): faces.append((fan[0],fan[j],fan[j+1]))
            fan = []
        else:
            fan.append(idx)
    if len(fan) >= 3:
        for j in range(1, len(fan)-1): faces.append((fan[0],fan[j],fan[j+1]))
    return np.array(faces, dtype=np.int64) if faces else np.empty((0,3),dtype=np.int64)


def _extract_ifs(text, cs, ce, brace_idx, bracket_idx, def_registry):
    # coordIndex — try inline then USE
    ci_m = _CI_RE.search(text, cs, ce)
    if ci_m:
        ci_pos = _bracket_pos(text, ci_m.start(), ce, bracket_idx)
        if not ci_pos: return None
        indices = [int(x) for x in re.findall(r'-?\d+', text[ci_pos[0]:ci_pos[1]])]
    else:
        # try geometry USE <name> pointing to another IFS
        use_m = _USE_RE.search(text, cs, min(cs+200, ce))
        if use_m and use_m.group(1) in def_registry:
            _, dcs, dce = def_registry[use_m.group(1)]
            ci_m2 = _CI_RE.search(text, dcs, dce)
            if ci_m2:
                ci_pos2 = _bracket_pos(text, ci_m2.start(), dce, bracket_idx)
                if not ci_pos2: return None
                indices = [int(x) for x in re.findall(r'-?\d+', text[ci_pos2[0]:ci_pos2[1]])]
                cs, ce = dcs, dce  # switch context to DEF block for point lookup
            else:
                return None
        else:
            return None

    if not indices: return None

    floats = _get_points(text, cs, ce, brace_idx, bracket_idx, def_registry)
    if floats is None or len(floats) < 9 or len(floats) % 3 != 0: return None
    verts = floats.reshape(-1, 3)

    faces = _build_faces(indices)
    if len(faces) == 0: return None
    valid = np.all((faces >= 0) & (faces < len(verts)), axis=1)
    faces = faces[valid]
    if len(faces) == 0: return None
    return verts, faces


# ── Iterative tree walker ───────────────────────────────────────────────────────────────────

def _process_shape(text, cs, ce, matrix, meshes, brace_idx, bracket_idx, def_registry, shape_pos):
    import trimesh
    ifs_cs, ifs_ce = cs, ce

    ifs_m = _IFS_RE.search(text, cs, ce)
    if ifs_m:
        bp2 = _brace_pos(text, ifs_m.start(), brace_idx)
        if bp2: ifs_cs, ifs_ce = bp2
    else:
        # geometry USE <name>
        use_m = _USE_RE.search(text, cs, min(cs+500, ce))
        if use_m and use_m.group(1) in def_registry:
            node_type, dcs, dce = def_registry[use_m.group(1)]
            if node_type == 'IndexedFaceSet':
                ifs_cs, ifs_ce = dcs, dce
            elif node_type == 'Shape':
                # recurse into the DEF'd Shape
                _process_shape(text, dcs, dce, matrix, meshes,
                               brace_idx, bracket_idx, def_registry, shape_pos)
                return
            else:
                return
        else:
            return

    result = _extract_ifs(text, ifs_cs, ifs_ce, brace_idx, bracket_idx, def_registry)
    if not result: return

    verts, faces = result
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    if not np.allclose(matrix, np.eye(4)):
        mesh.apply_transform(matrix)

    color = [200, 200, 200, 255]
    back_s = max(0, shape_pos - 10000)
    dc = _DC_RE.search(text, back_s, shape_pos)
    if dc:
        color = [int(float(dc.group(i))*255) for i in (1,2,3)] + [255]
        tr2 = _TP_RE.search(text, back_s, shape_pos)
        if tr2:
            color[3] = max(0, min(255, int((1-float(tr2.group(1)))*255)))

    mesh.visual.face_colors = color
    meshes.append(mesh)
    logger.info('  part: %d verts  %d faces  t=[%.2f,%.2f,%.2f]',
                len(verts), len(faces), matrix[0,3], matrix[1,3], matrix[2,3])


def _walk(text, meshes, brace_idx, bracket_idx, def_registry):
    seen_use = set()  # avoid processing same DEF geometry twice via multiple USE refs
    stack = [(0, len(text), np.eye(4))]

    while stack:
        start, end, matrix = stack.pop()
        pos = start

        while pos < end:
            # Check for bare USE statement (e.g. children containing USE NodeName)
            next_node = _NODE_RE.search(text, pos, end)
            next_use  = _STMT_USE_RE.search(text, pos, end)

            # Pick whichever comes first
            use_first = (next_use and
                         (next_node is None or next_use.start() < next_node.start()))

            if use_first:
                name = next_use.group(1)
                if name in def_registry and name not in seen_use:
                    seen_use.add(name)
                    node_type, dcs, dce = def_registry[name]
                    if node_type in _CONTAINER:
                        child_matrix = matrix
                        if node_type == 'Transform':
                            child_matrix = matrix @ _transform_matrix(text, dcs, dce)
                        stack.append((dcs, dce, child_matrix))
                    elif node_type == 'Shape':
                        _process_shape(text, dcs, dce, matrix, meshes,
                                       brace_idx, bracket_idx, def_registry,
                                       next_use.start())
                    elif node_type == 'IndexedFaceSet':
                        result = _extract_ifs(text, dcs, dce, brace_idx, bracket_idx, def_registry)
                        if result:
                            import trimesh
                            verts, faces = result
                            mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                            if not np.allclose(matrix, np.eye(4)):
                                mesh.apply_transform(matrix)
                            mesh.visual.face_colors = [200, 200, 200, 255]
                            meshes.append(mesh)
                pos = next_use.end()
                continue

            if next_node is None:
                break

            m = next_node
            node = m.group(1)
            bp = _brace_pos(text, m.start(), brace_idx)
            if not bp:
                pos = m.end()
                continue
            cs, ce = bp

            if node in _CONTAINER:
                child_matrix = matrix
                if node == 'Transform':
                    child_matrix = matrix @ _transform_matrix(text, cs, ce)
                stack.append((cs, ce, child_matrix))
                pos = ce + 1
                continue

            elif node in _SHAPE_LIKE:
                ifs_cs, ifs_ce = cs, ce
                if node == 'Shape':
                    _process_shape(text, cs, ce, matrix, meshes,
                                   brace_idx, bracket_idx, def_registry, m.start())
                else:  # IndexedFaceSet directly
                    result = _extract_ifs(text, cs, ce, brace_idx, bracket_idx, def_registry)
                    if result:
                        import trimesh
                        verts, faces = result
                        mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                        if not np.allclose(matrix, np.eye(4)):
                            mesh.apply_transform(matrix)
                        color = [200, 200, 200, 255]
                        back_s = max(0, m.start() - 10000)
                        dc = _DC_RE.search(text, back_s, m.start())
                        if dc:
                            color = [int(float(dc.group(i))*255) for i in (1,2,3)] + [255]
                        mesh.visual.face_colors = color
                        meshes.append(mesh)
                        logger.info('  part: %d verts  %d faces  t=[%.2f,%.2f,%.2f]',
                                    len(verts), len(faces), matrix[0,3], matrix[1,3], matrix[2,3])
                pos = ce + 1
                continue

            pos = ce + 1


def _parse_vrml(src: Path):
    import trimesh
    logger.info('Reading %s (%.1f MB) …', src.name, src.stat().st_size/1e6)
    text = src.read_text(encoding='utf-8', errors='replace')
    brace_idx    = _build_brace_index(text)
    bracket_idx  = _build_bracket_index(text)
    def_registry = _build_def_registry(text, brace_idx)
    logger.info('Walking VRML tree …')
    meshes = []
    _walk(text, meshes, brace_idx, bracket_idx, def_registry)
    if not meshes:
        raise ValueError('No IndexedFaceSet geometry found in the WRL file.')
    combined = trimesh.util.concatenate(meshes)
    logger.info('Combined: %d faces  %d verts  from %d parts',
                len(combined.faces), len(combined.vertices), len(meshes))
    return combined


def _simplify(mesh, max_faces):
    if len(mesh.faces) <= max_faces: return mesh
    logger.info('Simplifying to ~%d faces …', max_faces)
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


def _to_bytes(out):
    return out.encode('utf-8') if isinstance(out, str) else bytes(out)


# ── Routes ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/convert', methods=['POST'])
def convert():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(detail='No file received.'), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('wrl', 'vrml'):
        return jsonify(detail=f'Only .wrl/.vrml supported (got .{ext}).'), 400
    out_format = request.form.get('out_format', 'glb').lower()
    if out_format not in ('glb', 'obj', 'stl'): out_format = 'glb'
    max_faces = max(5000, min(int(request.form.get('max_faces', 200000)), 5_000_000))

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
            tmp_path = Path(tmp.name)
            f.save(tmp)
        mesh = _simplify(_parse_vrml(tmp_path), max_faces)
        raw = mesh.export(file_type=out_format)
        out_bytes = _to_bytes(raw)
        logger.info('Output: %.2f MB  %s', len(out_bytes)/1e6, out_format)
        mime = {'glb':'model/gltf-binary','obj':'text/plain','stl':'application/octet-stream'}[out_format]
        return send_file(io.BytesIO(out_bytes), mimetype=mime,
                         as_attachment=True, download_name=f'{Path(f.filename).stem}.{out_format}')
    except ValueError as ve:
        return jsonify(detail=str(ve)), 422
    except Exception:
        logger.error(traceback.format_exc())
        return jsonify(detail='Conversion failed — check terminal.'), 500
    finally:
        if tmp_path and tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass


if __name__ == '__main__':
    print('\n  VRML / WRL Converter  →  http://localhost:5555\n')
    app.run(host='0.0.0.0', port=5555, debug=False)
