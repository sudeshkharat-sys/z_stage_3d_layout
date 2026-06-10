"""VRML 1.0 / WRL → GLB / OBJ / STL Converter

Properly handles VRML 1.0 (Open Inventor) state machine:
  - Direct-child-only scanning per Separator
  - MatrixTransform / Transform applied at the correct scope level
  - Coordinate3 / Material / IndexedFaceSet sibling relationship respected

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


# ── Compiled patterns ─────────────────────────────────────────────────────

_FLT         = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
_NUM_RE      = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_BRACE_RE    = re.compile(r'[{}]')
_BRACKET_RE  = re.compile(r'[\[\]]')

_DIRECT_RE = re.compile(
    r'\b(MatrixTransform|Transform|Separator|Group|LOD|Switch'
    r'|TransformSeparator|Coordinate3|IndexedFaceSet|IndexedLineSet'
    r'|Material|MaterialBinding|Normal|NormalBinding|ShapeHints'
    r'|Info|Texture2|Texture2Transform|TextureCoordinate2'
    r'|PointLight|DirectionalLight|SpotLight|OrthographicCamera|PerspectiveCamera'
    r'|Cube|Sphere|Cone|Cylinder|AsciiText'
    r'|FontStyle|DrawStyle|LightModel|BaseColor|PackedColor'
    r'|EnvironmentMap|ShapeKit|WWWAnchor|WWWInline'
    r')\s*\{'
)

_PT_RE       = re.compile(r'\bpoint\s*\[')
_CI_RE       = re.compile(r'\bcoordIndex\s*\[')
_DC_RE       = re.compile(rf'diffuseColor\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_FLT16       = r'\s+'.join([rf'({_FLT})'] * 16)
_MTX_VALS_RE = re.compile(r'\bmatrix\s+' + _FLT16)
_TR1_RE      = re.compile(rf'\btranslation\s+({_FLT})\s+({_FLT})\s+({_FLT})')
_SC1_RE      = re.compile(rf'\bscaleFactor\s+({_FLT})(?:\s+({_FLT})\s+({_FLT}))?')
_RO1_RE      = re.compile(rf'\brotation\s+({_FLT})\s+({_FLT})\s+({_FLT})\s+({_FLT})')


# ── Index builders ────────────────────────────────────────────────────────────

def _build_brace_index(text):
    logger.info('Building brace index …')
    idx, stack = {}, []
    for m in _BRACE_RE.finditer(text):
        if m.group() == '{': stack.append(m.start())
        elif stack: idx[stack.pop()] = m.start()
    logger.info('Brace index: %d pairs', len(idx))
    return idx


def _build_bracket_index(text):
    idx, stack = {}, []
    for m in _BRACKET_RE.finditer(text):
        if m.group() == '[': stack.append(m.start())
        elif stack: idx[stack.pop()] = m.start()
    return idx


# ── Position helpers ────────────────────────────────────────────────────────────

def _brace_pos(text, start, brace_idx):
    p = text.find('{', start)
    if p == -1: return None
    cl = brace_idx.get(p)
    return (p + 1, cl) if cl is not None else None


def _bracket_pos(text, start, end, bracket_idx):
    p = text.find('[', start)
    if p == -1 or p >= end: return None
    cl = bracket_idx.get(p)
    return (p + 1, cl) if (cl is not None and cl <= end) else None


# ── Direct-child scanner ────────────────────────────────────────────────────────────

def _direct_children(text, cs, ce, brace_idx):
    """
    Yield (node_name, keyword_pos, content_cs, content_ce) for EVERY
    direct child node in range [cs, ce).  Nested content is skipped by
    jumping to brace_close+1 after each node.
    """
    pos = cs
    while pos < ce:
        m = _DIRECT_RE.search(text, pos, ce)
        if m is None:
            break
        node = m.group(1)
        brace_open = text.find('{', m.start())
        if brace_open == -1 or brace_open >= ce:
            pos = m.end()
            continue
        brace_close = brace_idx.get(brace_open)
        if brace_close is None or brace_close > ce:
            pos = m.end()
            continue
        yield node, m.start(), brace_open + 1, brace_close
        pos = brace_close + 1


# ── Math helpers ───────────────────────────────────────────────────────────────────────

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


# ── Face builder ──────────────────────────────────────────────────────────────

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


# ── VRML 1.0 state-machine walker ──────────────────────────────────────────────────────

_CONTAINERS = frozenset({'Separator', 'Group', 'Switch', 'TransformSeparator'})


def _walk(text, meshes, brace_idx, bracket_idx):
    import trimesh

    stack = [(0, len(text), np.eye(4))]
    total_tried = 0

    while stack:
        cs, ce, parent_matrix = stack.pop()

        current_matrix = np.copy(parent_matrix)
        current_coord  = None
        current_color  = [230, 230, 230, 255]  # light gray default

        for node, node_pos, ncs, nce in _direct_children(text, cs, ce, brace_idx):

            if node == 'MatrixTransform':
                vm = _MTX_VALS_RE.search(text, ncs, nce)
                if vm:
                    mat = np.array([float(vm.group(i)) for i in range(1, 17)],
                                   dtype=np.float64).reshape(4, 4)
                    current_matrix = parent_matrix @ mat

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
                current_matrix = parent_matrix @ M

            elif node == 'Coordinate3':
                pt_m = _PT_RE.search(text, ncs, nce)
                if pt_m:
                    pp = _bracket_pos(text, pt_m.start(), nce, bracket_idx)
                    if pp:
                        floats = _parse_floats(text, pp)
                        if floats is not None and len(floats) >= 9 and len(floats) % 3 == 0:
                            current_coord = floats.reshape(-1, 3)

            elif node == 'Material':
                hdr = text[ncs: min(ncs + 400, nce)]
                dc = _DC_RE.search(hdr)
                if dc:
                    current_color = [int(float(dc.group(i)) * 255) for i in (1, 2, 3)] + [255]

            elif node in _CONTAINERS:
                stack.append((ncs, nce, current_matrix))

            elif node == 'LOD':
                for child_node, _, ccs, cce in _direct_children(text, ncs, nce, brace_idx):
                    if child_node in _CONTAINERS or child_node == 'LOD':
                        stack.append((ccs, cce, current_matrix))
                        break

            elif node == 'IndexedFaceSet':
                total_tried += 1
                if current_coord is None:
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
                valid = np.all((faces >= 0) & (faces < len(current_coord)), axis=1)
                faces = faces[valid]
                if len(faces) == 0:
                    continue

                mesh = trimesh.Trimesh(vertices=current_coord.copy(),
                                       faces=faces, process=False)
                if not np.allclose(current_matrix, np.eye(4)):
                    mesh.apply_transform(current_matrix)
                mesh.visual.face_colors = current_color
                meshes.append(mesh)
                logger.info('  part %d: %d verts  %d faces  color=%s',
                            len(meshes), len(current_coord), len(faces), current_color[:3])

    logger.info('IFS tried: %d  succeeded: %d', total_tried, len(meshes))


def _parse_vrml(src: Path):
    import trimesh
    logger.info('Reading %s (%.1f MB) …', src.name, src.stat().st_size / 1e6)
    text = src.read_text(encoding='utf-8', errors='replace')
    brace_idx   = _build_brace_index(text)
    bracket_idx = _build_bracket_index(text)
    logger.info('Walking VRML 1.0 tree (state-machine) …')
    meshes = []
    _walk(text, meshes, brace_idx, bracket_idx)
    if not meshes:
        raise ValueError('No geometry found — check terminal for details.')
    logger.info('Concatenating %d meshes …', len(meshes))
    combined = trimesh.util.concatenate(meshes)
    logger.info('Combined: %d faces  %d verts', len(combined.faces), len(combined.vertices))
    return combined


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


def _to_bytes(out):
    return out.encode('utf-8') if isinstance(out, str) else bytes(out)


# ── Flask routes ────────────────────────────────────────────────────────────────────────

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
    if out_format not in ('glb', 'obj', 'stl'):
        out_format = 'glb'
    max_faces = max(5000, min(int(request.form.get('max_faces', 500000)), 10_000_000))

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
            tmp_path = Path(tmp.name)
            f.save(tmp)
        mesh = _simplify(_parse_vrml(tmp_path), max_faces)
        raw  = mesh.export(file_type=out_format)
        out_bytes = _to_bytes(raw)
        logger.info('Output: %.2f MB  format=%s', len(out_bytes) / 1e6, out_format)
        mime = {'glb': 'model/gltf-binary', 'obj': 'text/plain',
                'stl': 'application/octet-stream'}[out_format]
        return send_file(
            io.BytesIO(out_bytes), mimetype=mime,
            as_attachment=True,
            download_name=f'{Path(f.filename).stem}.{out_format}'
        )
    except ValueError as ve:
        return jsonify(detail=str(ve)), 422
    except Exception:
        logger.error(traceback.format_exc())
        return jsonify(detail='Conversion failed — check terminal.'), 500
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


if __name__ == '__main__':
    print('\n  VRML / WRL Converter  →  http://localhost:5555\n')
    app.run(host='0.0.0.0', port=5555, debug=False)
