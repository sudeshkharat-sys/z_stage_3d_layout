"""
VRML / WRL → GLB / OBJ / STL Converter  (position-based, no string copies)
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


# ── Transform math ───────────────────────────────────────────────────────────────────

_FLT = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
_NODE_RE = re.compile(
    r'\b(Transform|Group|Separator|Switch|Shape|IndexedFaceSet)\s*\{')
_IFS_RE  = re.compile(r'\bIndexedFaceSet\s*\{')
_NUM_RE  = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')


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
    """Parse translation/rotation/scale from text[cs:cs+2000] (no copy for large blocks)."""
    hdr = text[cs: min(cs+2000, ce)]   # only small header slice
    M = np.eye(4)
    tr = re.search(rf'\btranslation\s+({_FLT})\s+({_FLT})\s+({_FLT})', hdr)
    if tr:
        M[0,3]=float(tr.group(1)); M[1,3]=float(tr.group(2)); M[2,3]=float(tr.group(3))
    sc = re.search(rf'\bscale\s+({_FLT})\s+({_FLT})\s+({_FLT})', hdr)
    S = np.eye(4)
    if sc:
        S[0,0]=float(sc.group(1)); S[1,1]=float(sc.group(2)); S[2,2]=float(sc.group(3))
    ro = re.search(rf'\brotation\s+({_FLT})\s+({_FLT})\s+({_FLT})\s+({_FLT})', hdr)
    R = np.eye(4)
    if ro:
        R = _axis_angle_matrix(float(ro.group(1)),float(ro.group(2)),
                                float(ro.group(3)),float(ro.group(4)))
    return M @ R @ S


# ── Position-based brace/bracket finders (zero string copies) ───────────────────

def _brace_pos(text, start):
    """Return (content_start, content_end) of next { } block, or None."""
    p = text.find('{', start)
    if p == -1: return None
    depth = 0
    for i in range(p, len(text)):
        if text[i] == '{': depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0: return (p+1, i)
    return None


def _bracket_pos(text, start, end):
    """Return (content_start, content_end) of next [ ] block within text[start:end], or None."""
    p = text.find('[', start)
    if p == -1 or p >= end: return None
    depth = 0
    for i in range(p, end):
        if text[i] == '[': depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0: return (p+1, i)
    return None


# ── Geometry extraction ───────────────────────────────────────────────────────────────

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


def _extract_ifs(text, cs, ce):
    """
    Extract geometry from IndexedFaceSet block text[cs:ce].
    Returns (verts, faces) or None.
    All operations use positions; only small numeric slices are copied.
    """
    # coordIndex
    ci_m = re.search(r'\bcoordIndex\s*\[', text, cs, ce)
    if not ci_m: return None
    ci_pos = _bracket_pos(text, ci_m.start(), ce)
    if not ci_pos: return None
    indices = [int(x) for x in re.findall(r'-?\d+', text[ci_pos[0]:ci_pos[1]])]
    if not indices: return None

    # point — inside block
    pt_m = re.search(r'\bpoint\s*\[', text, cs, ce)
    if pt_m:
        pt_pos = _bracket_pos(text, pt_m.start(), ce)
    else:
        # search backwards up to 60 000 chars
        ws = max(0, cs - 60000)
        last = None
        for lm in re.finditer(r'\bpoint\s*\[', text[ws:cs]):
            last = lm
        if not last: return None
        abs_pt = ws + last.start()
        pt_pos = _bracket_pos(text, abs_pt, cs + 500)  # allow slight overlap

    if not pt_pos: return None
    floats = [float(x) for x in _NUM_RE.findall(text[pt_pos[0]:pt_pos[1]])]
    if len(floats) < 9 or len(floats) % 3 != 0: return None
    verts = np.array(floats, dtype=np.float64).reshape(-1, 3)

    faces = _build_faces(indices)
    if len(faces) == 0: return None
    valid = np.all((faces >= 0) & (faces < len(verts)), axis=1)
    faces = faces[valid]
    if len(faces) == 0: return None
    return verts, faces


# ── Iterative tree walker (no recursion = no stack overflow or string copies) ──────

def _walk(text, meshes):
    """
    Iterative VRML tree walk using an explicit stack of (search_start, search_end, matrix).
    Never copies the text string — all operations use start/end indices.
    """
    import trimesh

    # stack entries: (search_start, search_end, transform_matrix)
    stack = [(0, len(text), np.eye(4))]

    while stack:
        start, end, matrix = stack.pop()

        pos = start
        while pos < end:
            m = _NODE_RE.search(text, pos, end)
            if not m: break

            node = m.group(1)
            bp = _brace_pos(text, m.start())
            if not bp: break
            cs, ce = bp          # content start / end

            if node in ('Transform', 'Group', 'Separator', 'Switch'):
                child_matrix = matrix
                if node == 'Transform':
                    child_matrix = matrix @ _transform_matrix(text, cs, ce)
                # push children onto stack instead of recursing
                stack.append((cs, ce, child_matrix))
                pos = ce + 1     # skip past this block at this level
                continue

            elif node in ('Shape', 'IndexedFaceSet'):
                ifs_cs, ifs_ce = cs, ce
                if node == 'Shape':
                    ifs_m = _IFS_RE.search(text, cs, ce)
                    if ifs_m:
                        bp2 = _brace_pos(text, ifs_m.start())
                        if bp2: ifs_cs, ifs_ce = bp2
                    else:
                        pos = ce + 1
                        continue

                result = _extract_ifs(text, ifs_cs, ifs_ce)
                if result:
                    verts, faces = result
                    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                    if not np.allclose(matrix, np.eye(4)):
                        mesh.apply_transform(matrix)

                    # colour — search backwards 10 000 chars
                    color = [200, 200, 200, 255]
                    back_start = max(0, m.start() - 10000)
                    dc = re.search(
                        rf'diffuseColor\s+({_FLT})\s+({_FLT})\s+({_FLT})',
                        text, back_start, m.start())
                    if dc:
                        color = [int(float(dc.group(i))*255) for i in (1,2,3)] + [255]
                        tr2 = re.search(rf'transparency\s+({_FLT})',
                                        text, back_start, m.start())
                        if tr2:
                            color[3] = max(0, min(255, int((1-float(tr2.group(1)))*255)))

                    mesh.visual.face_colors = color
                    meshes.append(mesh)
                    logger.info('  part: %d verts  %d faces  t=[%.2f,%.2f,%.2f]',
                                len(verts), len(faces),
                                matrix[0,3], matrix[1,3], matrix[2,3])

                pos = ce + 1
                continue

            pos = ce + 1


def _parse_vrml(src: Path):
    import trimesh
    logger.info('Reading %s (%.1f MB) …', src.name, src.stat().st_size/1e6)
    text = src.read_text(encoding='utf-8', errors='replace')
    logger.info('Walking VRML tree (iterative) …')
    meshes = []
    _walk(text, meshes)
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


# ── Routes ─────────────────────────────────────────────────────────────────────

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
