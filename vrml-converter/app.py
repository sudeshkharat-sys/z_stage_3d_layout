"""
VRML / WRL → GLB / OBJ / STL Converter
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
    text-align: center; cursor: pointer; transition: border-color .2s, background .2s; margin-bottom: 1.5rem; }
  .drop-zone:hover, .drop-zone.dragover { border-color: #7dd3fc; background: #0f172a; }
  .drop-zone input[type=file] { display: none; }
  .drop-zone .icon { font-size: 2.5rem; margin-bottom: .5rem; }
  .drop-zone .hint { color: #64748b; font-size: .85rem; margin-top: .4rem; }
  .drop-zone .chosen { color: #7dd3fc; font-weight: 600; margin-top: .6rem; font-size: .95rem; }
  .row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
  .field { flex: 1; }
  select, input[type=number] { width: 100%; background: #0f172a; border: 1px solid #334155;
    border-radius: 8px; color: #e2e8f0; padding: .55rem .75rem; font-size: .9rem; }
  select:focus, input[type=number]:focus { outline: none; border-color: #7dd3fc; }
  button { width: 100%; background: #0284c7; color: #fff; border: none; border-radius: 10px;
    padding: .85rem; font-size: 1rem; font-weight: 600; cursor: pointer; transition: background .2s; }
  button:hover { background: #0369a1; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  .progress-wrap { margin-top: 1.5rem; display: none; }
  .progress-bar { background: #1e3a5f; border-radius: 8px; height: 10px; overflow: hidden; margin-bottom: .5rem; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, #0284c7, #7dd3fc);
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
      rb.innerHTML='&#9989; Done! Download started. <span class="stats">'+fmt(xhr.response.size)+'</span>';
    }else{xhr.response.text().then(t=>{let m='Conversion failed.';try{m=JSON.parse(t).detail||m;}catch(_){}rb.className='result err';rb.style.display='block';rb.textContent='✗ '+m;});}
  };
  xhr.onerror=()=>{clearInterval(tk);btn.disabled=false;rb.className='result err';rb.style.display='block';rb.textContent='✗ Network error.';};
  xhr.send(form);
}
</script>
</body></html>
"""


# ── VRML parser: find every IndexedFaceSet regardless of nesting ─────────────────
#
# Strategy that worked before:
#   1. Read whole file into memory (fast for 102 MB; feasible for 2 GB on modern HW)
#   2. Find every IndexedFaceSet { } block with balanced-brace extraction
#   3. Inside each block find coordIndex [ ] and the nearest Coordinate point [ ]
#   4. Also search backwards a small window for Material diffuseColor

def _balanced_brackets(text: str, start: int) -> str:
    """Return content of [ ] block opening at or after start."""
    p = text.find('[', start)
    if p == -1:
        return ''
    depth = 0
    for i in range(p, len(text)):
        if text[i] == '[': depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0:
                return text[p+1:i]
    return ''


def _balanced_braces(text: str, start: int) -> tuple:
    """Return (content, end_pos) of { } block opening at or after start."""
    p = text.find('{', start)
    if p == -1:
        return '', -1
    depth = 0
    for i in range(p, len(text)):
        if text[i] == '{': depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[p+1:i], i
    return '', -1


def _parse_floats(s: str) -> list:
    return [float(x) for x in re.findall(
        r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', s)]


def _parse_ints(s: str) -> list:
    return [int(x) for x in re.findall(r'-?\d+', s)]


def _build_faces(indices: list) -> np.ndarray:
    faces, fan = [], []
    for idx in indices:
        if idx < 0:
            if len(fan) >= 3:
                for j in range(1, len(fan)-1):
                    faces.append((fan[0], fan[j], fan[j+1]))
            fan = []
        else:
            fan.append(idx)
    if len(fan) >= 3:
        for j in range(1, len(fan)-1):
            faces.append((fan[0], fan[j], fan[j+1]))
    return np.array(faces, dtype=np.int64) if faces else np.empty((0,3), dtype=np.int64)


def _parse_vrml(src: Path):
    import trimesh

    logger.info('Reading %s (%.1f MB) …', src.name, src.stat().st_size/1e6)
    text = src.read_text(encoding='utf-8', errors='replace')
    logger.info('Scanning for IndexedFaceSet blocks …')

    meshes = []
    search_pos = 0

    for m in re.finditer(r'\bIndexedFaceSet\s*\{', text):
        ifs_start = m.start()
        block, end_pos = _balanced_braces(text, ifs_start)
        if not block:
            continue

        # ---- coordIndex ----
        ci_m = re.search(r'\bcoordIndex\s*\[', block)
        if not ci_m:
            continue
        ci_content = _balanced_brackets(block, ci_m.start())
        indices = _parse_ints(ci_content)
        if not indices:
            continue

        # ---- point (inside this block or in Coordinate just before) ----
        pt_content = ''
        pt_m = re.search(r'\bpoint\s*\[', block)
        if pt_m:
            pt_content = _balanced_brackets(block, pt_m.start())
        else:
            # look backwards up to 50 000 chars for a Coordinate block
            window_start = max(0, ifs_start - 50000)
            window = text[window_start:ifs_start]
            # find the LAST 'point [' in that window
            for pm in re.finditer(r'\bpoint\s*\[', window):
                pass
            else:
                pm = None
            for pm in re.finditer(r'\bpoint\s*\[', window):
                last_pm = pm
            # use last match
            try:
                pt_content = _balanced_brackets(window, last_pm.start())
            except Exception:
                continue

        floats = _parse_floats(pt_content)
        if len(floats) < 9 or len(floats) % 3 != 0:
            continue
        verts = np.array(floats, dtype=np.float64).reshape(-1, 3)

        faces = _build_faces(indices)
        if len(faces) == 0:
            continue
        valid = np.all((faces >= 0) & (faces < len(verts)), axis=1)
        faces = faces[valid]
        if len(faces) == 0:
            continue

        mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)

        # ---- material colour (search backwards 10 000 chars) ----
        color = [200, 200, 200, 255]
        back = text[max(0, ifs_start-10000):ifs_start]
        dc = re.search(
            r'diffuseColor\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)',
            back)
        if dc:
            color = [
                int(float(dc.group(1))*255),
                int(float(dc.group(2))*255),
                int(float(dc.group(3))*255),
                255,
            ]
            tr = re.search(r'transparency\s+([\d.eE+\-]+)', back)
            if tr:
                color[3] = max(0, min(255, int((1-float(tr.group(1)))*255)))

        mesh.visual.face_colors = color
        meshes.append(mesh)
        logger.info('  IFS: %d verts  %d faces  rgba=%s', len(verts), len(faces), color)

    if not meshes:
        raise ValueError('No IndexedFaceSet geometry found in the WRL file.')

    combined = trimesh.util.concatenate(meshes)
    logger.info('Combined: %d faces  %d verts  from %d meshes',
                len(combined.faces), len(combined.vertices), len(meshes))
    return combined


def _simplify(mesh, max_faces: int):
    if len(mesh.faces) <= max_faces:
        return mesh
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


def _to_bytes(out) -> bytes:
    return out.encode('utf-8') if isinstance(out, str) else bytes(out)


# ── Routes ────────────────────────────────────────────────────────────────────

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
        mime = {'glb': 'model/gltf-binary', 'obj': 'text/plain', 'stl': 'application/octet-stream'}[out_format]
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
