"""
VRML / WRL → GLB / OBJ / STL Converter  (streaming parser, handles large files)
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

# ── HTML (unchanged UI) ────────────────────────────────────────────────────────

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
let chosenFile = null;
const dz = document.getElementById('dropZone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('dragover'); const f=e.dataTransfer.files[0]; if(f) setFile(f); });
function onFileChosen(input) { if(input.files[0]) setFile(input.files[0]); }
function setFile(f) {
  chosenFile=f;
  document.getElementById('chosenName').textContent=f.name+'  ('+formatBytes(f.size)+')';
  document.getElementById('convertBtn').disabled=false;
}
function formatBytes(b) {
  if(b>1e9) return (b/1e9).toFixed(1)+' GB';
  if(b>1e6) return (b/1e6).toFixed(1)+' MB';
  return (b/1e3).toFixed(0)+' KB';
}
async function convert() {
  if(!chosenFile) return;
  const btn=document.getElementById('convertBtn'),pw=document.getElementById('progressWrap'),
        pf=document.getElementById('progressFill'),pl=document.getElementById('progressLabel'),
        rb=document.getElementById('resultBox');
  btn.disabled=true; rb.style.display='none'; pw.style.display='block'; pf.style.width='5%';
  pl.textContent='Uploading file…';
  const fmt=document.getElementById('outFmt').value,
        maxFaces=parseInt(document.getElementById('maxFaces').value)||200000,
        form=new FormData();
  form.append('file',chosenFile); form.append('out_format',fmt); form.append('max_faces',maxFaces);
  const xhr=new XMLHttpRequest(); xhr.open('POST','/convert'); xhr.responseType='blob';
  xhr.upload.onprogress=e=>{ if(e.lengthComputable){const p=Math.round(e.loaded/e.total*50); pf.style.width=p+'%'; pl.textContent='Uploading… '+p+'%';} };
  let fakeP=50;
  const ticker=setInterval(()=>{ fakeP=Math.min(fakeP+(fakeP<70?2:fakeP<88?.8:.2),94); pf.style.width=fakeP+'%'; pl.textContent='Converting… '+Math.round(fakeP)+'%'; },600);
  xhr.onload=()=>{
    clearInterval(ticker); pf.style.width='100%'; pl.textContent='Done!'; btn.disabled=false;
    if(xhr.status===200){
      const url=URL.createObjectURL(xhr.response),base=chosenFile.name.replace(/\\.[^.]+$/,''),a=document.createElement('a');
      a.href=url; a.download=base+'.'+fmt; a.click(); URL.revokeObjectURL(url);
      rb.className='result ok'; rb.style.display='block';
      rb.innerHTML='&#9989; Conversion complete! Download started.<br/><span class="stats">Output size: '+formatBytes(xhr.response.size)+'</span>';
    } else {
      xhr.response.text().then(txt=>{ let msg='Conversion failed.'; try{msg=JSON.parse(txt).detail||msg;}catch(_){} rb.className='result err'; rb.style.display='block'; rb.textContent='✗ '+msg; });
    }
  };
  xhr.onerror=()=>{ clearInterval(ticker); btn.disabled=false; rb.className='result err'; rb.style.display='block'; rb.textContent='✗ Network error.'; };
  xhr.send(form);
}
</script>
</body></html>
"""


# ── Streaming VRML parser ──────────────────────────────────────────────────────────
#
# Reads the file line-by-line so even a 2 GB WRL never fully lives in RAM
# as a single regex target.  Tracks brace/bracket depth to know which
# section we are inside and collects tokens accordingly.

_NUM = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_INT = re.compile(r'-?\d+')


def _tokens_float(s):
    return [float(x) for x in _NUM.findall(s)]


def _tokens_int(s):
    return [int(x) for x in _INT.findall(s)]


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


class _VRMLStreamParser:
    """
    One-pass line-by-line state machine.
    Tracks nested braces so it knows exactly which block it is inside.
    """

    def __init__(self):
        # brace stack: list of context labels
        self._stack = []          # e.g. ['Shape', 'appearance', 'Appearance', 'material', 'Material']
        self._bracket_ctx = None  # which field is being collected inside [ ]
        self._bracket_buf = []    # token accumulator for current [ ] block
        self._bracket_depth = 0

        # per-Shape accumulators
        self._reset_shape()

        self.meshes = []

    def _reset_shape(self):
        self._points = []
        self._coord_index = []
        self._color = [200, 200, 200, 255]
        self._in_shape = False
        self._in_ifs = False
        self._in_appearance = False
        self._in_material = False
        self._in_coordinate = False

    def _context(self):
        return self._stack[-1] if self._stack else ''

    def _emit_shape(self):
        """Build a Trimesh from accumulated data and reset."""
        import trimesh
        if self._points and self._coord_index:
            verts = np.array(self._points, dtype=np.float64).reshape(-1, 3)
            faces_list = _faces_from_coord_index(self._coord_index)
            if faces_list:
                faces = np.array(faces_list, dtype=np.int64)
                valid = np.all((faces >= 0) & (faces < len(verts)), axis=1)
                faces = faces[valid]
                if len(faces) > 0:
                    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                    mesh.visual.face_colors = self._color
                    self.meshes.append(mesh)
                    logger.info("  Shape: %d verts %d faces  rgba=%s",
                                len(verts), len(faces), self._color)
        self._reset_shape()

    def feed_line(self, line: str):
        # Strip inline comments
        comment = line.find('#')
        if comment != -1:
            line = line[:comment]

        i = 0
        while i < len(line):
            ch = line[i]

            # ─ inside a bracket block: accumulate until depth==0 ─
            if self._bracket_depth > 0 or (self._bracket_ctx and ch == '['):
                if ch == '[':
                    self._bracket_depth += 1
                    i += 1
                    continue
                if ch == ']':
                    self._bracket_depth -= 1
                    if self._bracket_depth == 0:
                        # bracket closed: store collected tokens
                        raw = ''.join(self._bracket_buf)
                        if self._bracket_ctx == 'point':
                            self._points = _tokens_float(raw)
                        elif self._bracket_ctx == 'coordIndex':
                            self._coord_index = _tokens_int(raw)
                        self._bracket_buf = []
                        self._bracket_ctx = None
                    i += 1
                    continue
                self._bracket_buf.append(ch)
                i += 1
                continue

            # ─ brace open ─
            if ch == '{':
                # peek at the word just before '{' on the stack
                self._stack.append(self._pending_keyword or '?')
                self._pending_keyword = None

                ctx = self._context()
                if ctx == 'Shape':
                    self._in_shape = True
                elif ctx == 'IndexedFaceSet' and self._in_shape:
                    self._in_ifs = True
                elif ctx == 'Appearance' and self._in_shape:
                    self._in_appearance = True
                elif ctx == 'Material' and self._in_appearance:
                    self._in_material = True
                elif ctx == 'Coordinate' and self._in_ifs:
                    self._in_coordinate = True
                i += 1
                continue

            # ─ brace close ─
            if ch == '}':
                ctx = self._context()
                if ctx == 'Shape' and self._in_shape:
                    self._emit_shape()
                elif ctx == 'IndexedFaceSet':
                    self._in_ifs = False
                elif ctx == 'Appearance':
                    self._in_appearance = False
                elif ctx == 'Material':
                    self._in_material = False
                elif ctx == 'Coordinate':
                    self._in_coordinate = False
                if self._stack:
                    self._stack.pop()
                i += 1
                continue

            # ─ bracket open (field value) ─
            if ch == '[':
                self._bracket_depth = 1
                self._bracket_buf = []
                # determine which field this bracket belongs to
                # read word backward from current position on the line
                before = line[:i].rstrip()
                word_m = re.search(r'(\w+)\s*$', before)
                field = word_m.group(1) if word_m else ''
                if self._in_coordinate and field == 'point':
                    self._bracket_ctx = 'point'
                elif self._in_ifs and field == 'coordIndex':
                    self._bracket_ctx = 'coordIndex'
                else:
                    self._bracket_ctx = '__skip__'
                i += 1
                continue

            i += 1

        # ─ scan for keywords on this line (outside brackets) ─
        # We need to track the last keyword seen before a '{'
        # Do a lightweight scan for known node names
        if self._bracket_depth == 0:
            # collect last identifier token as pending keyword
            tokens = re.findall(r'[A-Za-z_]\w*', line)
            for tok in tokens:
                self._pending_keyword = tok
            # inline field values (no bracket): diffuseColor, transparency
            if self._in_material:
                m = re.search(r'diffuseColor\s+(' + _NUM.pattern + r')\s+(' + _NUM.pattern + r')\s+(' + _NUM.pattern + r')', line)
                if m:
                    r_, g_, b_ = float(m.group(1)), float(m.group(2)), float(m.group(3))
                    self._color = [int(r_*255), int(g_*255), int(b_*255), self._color[3]]
                t = re.search(r'transparency\s+(' + _NUM.pattern + r')', line)
                if t:
                    alpha = max(0, min(255, int((1.0 - float(t.group(1))) * 255)))
                    self._color[3] = alpha

    def parse_file(self, src: Path):
        self._pending_keyword = None
        logger.info("Streaming parse of %s (%.1f MB) …", src.name, src.stat().st_size / 1e6)
        with src.open(encoding='utf-8', errors='replace') as fh:
            for lineno, line in enumerate(fh, 1):
                self.feed_line(line)
                if lineno % 500_000 == 0:
                    logger.info("  … %d lines read, %d shapes so far", lineno, len(self.meshes))
        # flush any unclosed Shape
        if self._in_shape:
            self._emit_shape()
        logger.info("Parse complete: %d shapes found", len(self.meshes))


# ── Top-level load ───────────────────────────────────────────────────────────────────

def _parse_vrml(src: Path):
    import trimesh

    parser = _VRMLStreamParser()
    parser.parse_file(src)

    if not parser.meshes:
        raise ValueError("No IndexedFaceSet geometry found in the WRL file.")

    combined = trimesh.util.concatenate(parser.meshes)
    logger.info("Combined: %d faces, %d vertices", len(combined.faces), len(combined.vertices))
    return combined


def _simplify(mesh, max_faces: int):
    if len(mesh.faces) <= max_faces:
        return mesh
    logger.info("Simplifying to ~%d faces …", max_faces)
    for method in ('simplify_quadric_decimation', 'simplify_quadratic_decimation'):
        if hasattr(mesh, method):
            try:
                result = getattr(mesh, method)(max_faces)
                logger.info("After simplification: %d faces", len(result.faces))
                return result
            except Exception as exc:
                logger.warning("Simplification failed (%s) — skipping", exc)
                return mesh
    return mesh


def _load_and_simplify(src: Path, max_faces: int):
    mesh = _parse_vrml(src)
    return _simplify(mesh, max_faces)


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
        return jsonify(detail=f"Only .wrl / .vrml files are supported (got .{ext})."), 400

    out_format = request.form.get("out_format", "glb").lower()
    if out_format not in ("glb", "obj", "stl"):
        out_format = "glb"

    max_faces = int(request.form.get("max_faces", 200000))
    max_faces = max(5000, min(max_faces, 5_000_000))

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            f.save(tmp)

        mesh = _load_and_simplify(tmp_path, max_faces)

        raw = mesh.export(file_type=out_format)
        out_bytes = _to_bytes(raw)
        logger.info("Output: %.2f MB  format=%s", len(out_bytes) / 1e6, out_format)

        mime = {"glb": "model/gltf-binary", "obj": "text/plain", "stl": "application/octet-stream"}[out_format]
        stem = Path(f.filename).stem
        return send_file(io.BytesIO(out_bytes), mimetype=mime,
                         as_attachment=True, download_name=f"{stem}.{out_format}")

    except ValueError as ve:
        return jsonify(detail=str(ve)), 422
    except Exception:
        logger.error(traceback.format_exc())
        return jsonify(detail="Conversion failed — check terminal for details."), 500
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    print("\n  VRML / WRL Converter running at  http://localhost:5555\n")
    app.run(host="0.0.0.0", port=5555, debug=False)
