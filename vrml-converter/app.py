"""
VRML / WRL → GLB / OBJ / STL Converter  (streaming parser v2)
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

# ── HTML ───────────────────────────────────────────────────────────────────

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
  <p class="sub">Runs locally on CPU &mdash; no GPU needed &mdash; converts .wrl to .glb / .obj / .stl</p>
  <label>1. Choose your WRL / VRML file</label>
  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <input type="file" id="fileInput" accept=".wrl,.vrml" onchange="onFileChosen(this)"/>
    <div class="icon">&#128196;</div>
    <div>Click to browse or drag &amp; drop</div>
    <div class="hint">Supports .wrl &amp; .vrml &mdash; any size</div>
    <div class="chosen" id="chosenName"></div>
  </div>
  <div class="row">
    <div class="field">
      <label>2. Output format</label>
      <select id="outFmt">
        <option value="glb">GLB (recommended for 3D viewers)</option>
        <option value="obj">OBJ (compatible with most apps)</option>
        <option value="stl">STL</option>
      </select>
    </div>
    <div class="field">
      <label>3. Max faces after simplify</label>
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
function setFile(f){chosenFile=f;document.getElementById('chosenName').textContent=f.name+'  ('+formatBytes(f.size)+')';document.getElementById('convertBtn').disabled=false;}
function formatBytes(b){if(b>1e9)return(b/1e9).toFixed(1)+' GB';if(b>1e6)return(b/1e6).toFixed(1)+' MB';return(b/1e3).toFixed(0)+' KB';}
async function convert(){
  if(!chosenFile)return;
  const btn=document.getElementById('convertBtn'),pw=document.getElementById('progressWrap'),
        pf=document.getElementById('progressFill'),pl=document.getElementById('progressLabel'),
        rb=document.getElementById('resultBox');
  btn.disabled=true;rb.style.display='none';pw.style.display='block';pf.style.width='5%';pl.textContent='Uploading file…';
  const fmt=document.getElementById('outFmt').value,maxFaces=parseInt(document.getElementById('maxFaces').value)||200000,form=new FormData();
  form.append('file',chosenFile);form.append('out_format',fmt);form.append('max_faces',maxFaces);
  const xhr=new XMLHttpRequest();xhr.open('POST','/convert');xhr.responseType='blob';
  xhr.upload.onprogress=e=>{if(e.lengthComputable){const p=Math.round(e.loaded/e.total*50);pf.style.width=p+'%';pl.textContent='Uploading… '+p+'%';}};
  let fakeP=50;
  const ticker=setInterval(()=>{fakeP=Math.min(fakeP+(fakeP<70?2:fakeP<88?.8:.2),94);pf.style.width=fakeP+'%';pl.textContent='Converting… '+Math.round(fakeP)+'%';},600);
  xhr.onload=()=>{
    clearInterval(ticker);pf.style.width='100%';pl.textContent='Done!';btn.disabled=false;
    if(xhr.status===200){
      const url=URL.createObjectURL(xhr.response),base=chosenFile.name.replace(/\\.[^.]+$/,''),a=document.createElement('a');
      a.href=url;a.download=base+'.'+fmt;a.click();URL.revokeObjectURL(url);
      rb.className='result ok';rb.style.display='block';
      rb.innerHTML='&#9989; Conversion complete! Download started.<br/><span class="stats">Output: '+formatBytes(xhr.response.size)+'</span>';
    }else{xhr.response.text().then(txt=>{let msg='Conversion failed.';try{msg=JSON.parse(txt).detail||msg;}catch(_){}rb.className='result err';rb.style.display='block';rb.textContent='✗ '+msg;});}
  };
  xhr.onerror=()=>{clearInterval(ticker);btn.disabled=false;rb.className='result err';rb.style.display='block';rb.textContent='✗ Network error.';};
  xhr.send(form);
}
</script>
</body></html>
"""


# ── Streaming VRML parser v2 ──────────────────────────────────────────────────────────
#
# Key fix: when we hit '{', look BACKWARDS on the current line for the
# last identifier.  If the line is just '{', fall back to _last_word
# carried from the previous line.  This avoids the off-by-one bug in v1
# where _pending_keyword was set at end-of-line, after the '{' was
# already processed.

_NUM_RE  = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_WORD_RE = re.compile(r'[A-Za-z_]\w*')


def _last_word_before(line: str, pos: int) -> str:
    """Last identifier on `line` strictly before index `pos`."""
    m = re.search(r'([A-Za-z_]\w*)\s*$', line[:pos])
    return m.group(1) if m else ''


def _faces_from_coord_index(indices):
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
    return faces


class _VRMLParser:

    def __init__(self):
        self._stack     = []       # keyword pushed for each '{'
        self._last_word = ''       # last identifier seen (for next-line '{')

        # bracket accumulator
        self._bdepth = 0
        self._bctx   = None        # 'point' | 'coordIndex' | None
        self._bbuf   = []

        # current Shape state
        self._points      = None
        self._coord_index = None
        self._color       = [200, 200, 200, 255]

        # context flags (derived from stack)
        self._in_shape      = False
        self._in_ifs        = False   # IndexedFaceSet
        self._in_appearance = False
        self._in_material   = False
        self._in_coordinate = False

        self.meshes = []

    # ------------------------------------------------------------------ #

    def _stack_top(self):
        return self._stack[-1] if self._stack else ''

    def _emit(self):
        """Build mesh from accumulated Shape data."""
        import trimesh
        pts = self._points
        idx = self._coord_index
        if pts and idx:
            verts = np.array(pts, dtype=np.float64).reshape(-1, 3)
            face_list = _faces_from_coord_index(idx)
            if face_list:
                faces = np.array(face_list, dtype=np.int64)
                valid = np.all((faces >= 0) & (faces < len(verts)), axis=1)
                faces = faces[valid]
                if len(faces):
                    m = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                    m.visual.face_colors = list(self._color)
                    self.meshes.append(m)
                    logger.info("  Shape: %d verts  %d faces  rgba=%s",
                                len(verts), len(faces), self._color)
        # reset per-shape data
        self._points      = None
        self._coord_index = None
        self._color       = [200, 200, 200, 255]
        self._in_shape      = False
        self._in_ifs        = False
        self._in_appearance = False
        self._in_material   = False
        self._in_coordinate = False

    # ------------------------------------------------------------------ #

    def feed_line(self, raw: str):
        # strip comment
        c = raw.find('#')
        line = raw[:c] if c != -1 else raw

        i = 0
        n = len(line)

        while i < n:
            ch = line[i]

            # ---- inside bracket accumulator ----
            if self._bdepth > 0:
                if ch == '[':
                    self._bdepth += 1
                elif ch == ']':
                    self._bdepth -= 1
                    if self._bdepth == 0:
                        raw_buf = ''.join(self._bbuf)
                        if self._bctx == 'point':
                            self._points = [float(x) for x in _NUM_RE.findall(raw_buf)]
                        elif self._bctx == 'coordIndex':
                            self._coord_index = [int(x) for x in re.findall(r'-?\d+', raw_buf)]
                        self._bbuf  = []
                        self._bctx  = None
                else:
                    self._bbuf.append(ch)
                i += 1
                continue

            # ---- brace open ----
            if ch == '{':
                kw = _last_word_before(line, i) or self._last_word
                self._stack.append(kw)

                if kw == 'Shape':
                    self._in_shape = True
                elif kw == 'IndexedFaceSet' and self._in_shape:
                    self._in_ifs = True
                elif kw == 'Appearance' and self._in_shape:
                    self._in_appearance = True
                elif kw == 'Material' and self._in_appearance:
                    self._in_material = True
                elif kw == 'Coordinate' and self._in_ifs:
                    self._in_coordinate = True

                i += 1
                continue

            # ---- brace close ----
            if ch == '}':
                top = self._stack_top()
                if top == 'Shape':
                    self._emit()
                elif top == 'IndexedFaceSet':
                    self._in_ifs = False
                elif top == 'Appearance':
                    self._in_appearance = False
                elif top == 'Material':
                    self._in_material = False
                elif top == 'Coordinate':
                    self._in_coordinate = False
                if self._stack:
                    self._stack.pop()
                i += 1
                continue

            # ---- bracket open ----
            if ch == '[':
                self._bdepth = 1
                self._bbuf   = []
                field = _last_word_before(line, i)
                if field == 'point' and (self._in_coordinate or self._in_ifs):
                    self._bctx = 'point'
                elif field == 'coordIndex' and self._in_ifs:
                    self._bctx = 'coordIndex'
                else:
                    self._bctx = None   # skip
                i += 1
                continue

            i += 1

        # track last word on this line (used when next line is just '{')
        words = _WORD_RE.findall(line)
        if words:
            self._last_word = words[-1]

        # material properties (only meaningful inside Material {})
        if self._in_material:
            m = re.search(
                r'diffuseColor\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)', line)
            if m:
                self._color = [
                    int(float(m.group(1)) * 255),
                    int(float(m.group(2)) * 255),
                    int(float(m.group(3)) * 255),
                    self._color[3],
                ]
            t = re.search(r'transparency\s+([\d.eE+\-]+)', line)
            if t:
                self._color[3] = max(0, min(255, int((1.0 - float(t.group(1))) * 255)))

    # ------------------------------------------------------------------ #

    def parse_file(self, src: Path):
        logger.info("Streaming %s  (%.1f MB) …", src.name, src.stat().st_size / 1e6)
        with src.open(encoding='utf-8', errors='replace') as fh:
            for lineno, line in enumerate(fh, 1):
                self.feed_line(line)
                if lineno % 500_000 == 0:
                    logger.info("  … %d lines  %d shapes so far", lineno, len(self.meshes))
        if self._in_shape:      # flush unclosed Shape at EOF
            self._emit()
        logger.info("Parse done: %d shapes", len(self.meshes))


# ── Load + simplify ─────────────────────────────────────────────────────────────────

def _parse_vrml(src: Path):
    import trimesh
    p = _VRMLParser()
    p.parse_file(src)
    if not p.meshes:
        raise ValueError("No IndexedFaceSet geometry found in the WRL file.")
    combined = trimesh.util.concatenate(p.meshes)
    logger.info("Combined: %d faces  %d verts", len(combined.faces), len(combined.vertices))
    return combined


def _simplify(mesh, max_faces: int):
    if len(mesh.faces) <= max_faces:
        return mesh
    logger.info("Simplifying to ~%d faces …", max_faces)
    for method in ('simplify_quadric_decimation', 'simplify_quadratic_decimation'):
        if hasattr(mesh, method):
            try:
                r = getattr(mesh, method)(max_faces)
                logger.info("After simplification: %d faces", len(r.faces))
                return r
            except Exception as exc:
                logger.warning("Simplification skipped: %s", exc)
                return mesh
    return mesh


def _load_and_simplify(src: Path, max_faces: int):
    return _simplify(_parse_vrml(src), max_faces)


def _to_bytes(out) -> bytes:
    return out.encode('utf-8') if isinstance(out, str) else bytes(out)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/convert", methods=["POST"])
def convert():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(detail="No file received."), 400
    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("wrl", "vrml"):
        return jsonify(detail=f"Only .wrl/.vrml supported (got .{ext})."), 400
    out_format = request.form.get("out_format", "glb").lower()
    if out_format not in ("glb", "obj", "stl"):
        out_format = "glb"
    max_faces = max(5000, min(int(request.form.get("max_faces", 200000)), 5_000_000))

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            f.save(tmp)
        mesh = _load_and_simplify(tmp_path, max_faces)
        raw  = mesh.export(file_type=out_format)
        out_bytes = _to_bytes(raw)
        logger.info("Output: %.2f MB  %s", len(out_bytes) / 1e6, out_format)
        mime = {"glb": "model/gltf-binary", "obj": "text/plain", "stl": "application/octet-stream"}[out_format]
        return send_file(io.BytesIO(out_bytes), mimetype=mime,
                         as_attachment=True, download_name=f"{Path(f.filename).stem}.{out_format}")
    except ValueError as ve:
        return jsonify(detail=str(ve)), 422
    except Exception:
        logger.error(traceback.format_exc())
        return jsonify(detail="Conversion failed — check terminal."), 500
    finally:
        if tmp_path and tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass


if __name__ == "__main__":
    print("\n  VRML / WRL Converter  →  http://localhost:5555\n")
    app.run(host="0.0.0.0", port=5555, debug=False)
