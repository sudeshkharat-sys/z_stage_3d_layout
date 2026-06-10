"""
VRML / WRL → GLB / OBJ / STL Converter  (with material colours)
Standalone local web app — runs on CPU, no GPU needed.
Usage:
    pip install flask trimesh[easy] numpy
    python app.py
Then open http://localhost:5555 in your browser.
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
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB

# ── HTML ─────────────────────────────────────────────────────────────────────

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>VRML / WRL Converter</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', sans-serif;
    background: #0f172a; color: #e2e8f0;
    min-height: 100vh; display: flex;
    align-items: center; justify-content: center; padding: 2rem;
  }
  .card {
    background: #1e293b; border: 1px solid #334155;
    border-radius: 16px; padding: 2.5rem;
    width: 100%; max-width: 560px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }
  h1 { font-size: 1.6rem; color: #7dd3fc; margin-bottom: .25rem; }
  .sub { color: #94a3b8; font-size: .9rem; margin-bottom: 2rem; }
  label { display: block; font-size: .85rem; color: #94a3b8; margin-bottom: .4rem; }
  .drop-zone {
    border: 2px dashed #334155; border-radius: 12px;
    padding: 2.5rem; text-align: center; cursor: pointer;
    transition: border-color .2s, background .2s; margin-bottom: 1.5rem;
  }
  .drop-zone:hover, .drop-zone.dragover { border-color: #7dd3fc; background: #0f172a; }
  .drop-zone input[type=file] { display: none; }
  .drop-zone .icon { font-size: 2.5rem; margin-bottom: .5rem; }
  .drop-zone .hint { color: #64748b; font-size: .85rem; margin-top: .4rem; }
  .drop-zone .chosen { color: #7dd3fc; font-weight: 600; margin-top: .6rem; font-size: .95rem; }
  .row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
  .field { flex: 1; }
  select, input[type=number] {
    width: 100%; background: #0f172a; border: 1px solid #334155;
    border-radius: 8px; color: #e2e8f0; padding: .55rem .75rem; font-size: .9rem;
  }
  select:focus, input[type=number]:focus { outline: none; border-color: #7dd3fc; }
  button {
    width: 100%; background: #0284c7; color: #fff; border: none;
    border-radius: 10px; padding: .85rem; font-size: 1rem;
    font-weight: 600; cursor: pointer; transition: background .2s;
  }
  button:hover { background: #0369a1; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  .progress-wrap { margin-top: 1.5rem; display: none; }
  .progress-bar { background: #1e3a5f; border-radius: 8px; height: 10px; overflow: hidden; margin-bottom: .5rem; }
  .progress-fill {
    height: 100%; background: linear-gradient(90deg, #0284c7, #7dd3fc);
    border-radius: 8px; width: 0%; transition: width .3s ease;
  }
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
      <input type="number" id="maxFaces" value="80000" min="5000" max="2000000" step="5000"/>
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
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('dragover');
  const f = e.dataTransfer.files[0]; if (f) setFile(f);
});
function onFileChosen(input) { if (input.files[0]) setFile(input.files[0]); }
function setFile(f) {
  chosenFile = f;
  document.getElementById('chosenName').textContent = f.name + '  (' + formatBytes(f.size) + ')';
  document.getElementById('convertBtn').disabled = false;
}
function formatBytes(b) {
  if (b > 1e9) return (b/1e9).toFixed(1) + ' GB';
  if (b > 1e6) return (b/1e6).toFixed(1) + ' MB';
  return (b/1e3).toFixed(0) + ' KB';
}
async function convert() {
  if (!chosenFile) return;
  const btn = document.getElementById('convertBtn');
  const pw = document.getElementById('progressWrap');
  const pf = document.getElementById('progressFill');
  const pl = document.getElementById('progressLabel');
  const rb = document.getElementById('resultBox');
  btn.disabled = true; rb.style.display = 'none';
  pw.style.display = 'block'; pf.style.width = '5%';
  pl.textContent = 'Uploading file…';
  const fmt = document.getElementById('outFmt').value;
  const maxFaces = parseInt(document.getElementById('maxFaces').value) || 80000;
  const form = new FormData();
  form.append('file', chosenFile);
  form.append('out_format', fmt);
  form.append('max_faces', maxFaces);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/convert'); xhr.responseType = 'blob';
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 50);
      pf.style.width = pct + '%'; pl.textContent = 'Uploading… ' + pct + '%';
    }
  };
  let fakeP = 50;
  const ticker = setInterval(() => {
    fakeP = Math.min(fakeP + (fakeP < 70 ? 2 : fakeP < 88 ? 0.8 : 0.2), 94);
    pf.style.width = fakeP + '%';
    pl.textContent = 'Converting on server… ' + Math.round(fakeP) + '%';
  }, 600);
  xhr.onload = () => {
    clearInterval(ticker); pf.style.width = '100%'; pl.textContent = 'Done!'; btn.disabled = false;
    if (xhr.status === 200) {
      const url = URL.createObjectURL(xhr.response);
      const base = chosenFile.name.replace(/\\.[^.]+$/, '');
      const a = document.createElement('a');
      a.href = url; a.download = base + '.' + fmt; a.click();
      URL.revokeObjectURL(url);
      rb.className = 'result ok'; rb.style.display = 'block';
      rb.innerHTML = '&#9989; Conversion complete! Download started.<br/>'
        + '<span class="stats">Output size: ' + formatBytes(xhr.response.size) + '</span>';
    } else {
      xhr.response.text().then(txt => {
        let msg = 'Conversion failed.';
        try { msg = JSON.parse(txt).detail || msg; } catch(_) {}
        rb.className = 'result err'; rb.style.display = 'block'; rb.textContent = '✗ ' + msg;
      });
    }
  };
  xhr.onerror = () => {
    clearInterval(ticker); btn.disabled = false;
    rb.className = 'result err'; rb.style.display = 'block';
    rb.textContent = '✗ Network error. Is the server running?';
  };
  xhr.send(form);
}
</script>
</body>
</html>
"""


# ── VRML parser helpers ──────────────────────────────────────────────────────────

def _extract_bracket_content(text: str, start: int) -> str:
    """Return text inside the [ ] block at or after `start`."""
    open_pos = text.find('[', start)
    if open_pos == -1:
        return ""
    depth = 0
    for i in range(open_pos, len(text)):
        if text[i] == '[':
            depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0:
                return text[open_pos + 1:i]
    return ""


def _extract_brace_block(text: str, start: int) -> str:
    """Return text inside the { } block at or after `start`."""
    open_pos = text.find('{', start)
    if open_pos == -1:
        return ""
    depth = 0
    for i in range(open_pos, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[open_pos + 1:i]
    return ""


def _parse_floats(s: str) -> np.ndarray:
    return np.fromstring(re.sub(r'[,\n\r\t]', ' ', s), dtype=np.float64, sep=' ')


def _parse_ints(s: str) -> list:
    return [int(x) for x in re.findall(r'-?\d+', s)]


def _faces_from_index(indices: list) -> np.ndarray:
    faces = []
    fan = []
    for idx in indices:
        if idx == -1:
            if len(fan) >= 3:
                for j in range(1, len(fan) - 1):
                    faces.append([fan[0], fan[j], fan[j + 1]])
            fan = []
        else:
            fan.append(idx)
    if len(fan) >= 3:
        for j in range(1, len(fan) - 1):
            faces.append([fan[0], fan[j], fan[j + 1]])
    return np.array(faces, dtype=np.int64) if faces else np.empty((0, 3), dtype=np.int64)


def _parse_diffuse_color(block: str):
    """Return (r, g, b) 0-1 floats from a Material block, or None."""
    m = re.search(
        r'diffuseColor\s+(\S+)\s+(\S+)\s+(\S+)', block
    )
    if m:
        try:
            return (float(m.group(1)), float(m.group(2)), float(m.group(3)))
        except ValueError:
            pass
    return None


def _parse_transparency(block: str) -> float:
    m = re.search(r'transparency\s+(\S+)', block)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 0.0


# ── Main VRML parser ───────────────────────────────────────────────────────────────

def _parse_vrml(src: Path):
    import trimesh

    logger.info("Reading %s (%.1f MB) …", src.name, src.stat().st_size / 1e6)
    text = src.read_text(encoding='utf-8', errors='replace')
    logger.info("Parsing geometry and materials …")

    meshes = []

    # Iterate over every Shape { } block
    for shape_m in re.finditer(r'\bShape\s*\{', text):
        shape_block = _extract_brace_block(text, shape_m.start())
        if not shape_block:
            continue

        # ─ geometry: IndexedFaceSet only ─
        ifs_m = re.search(r'\bIndexedFaceSet\s*\{', shape_block)
        if not ifs_m:
            continue
        ifs_block = _extract_brace_block(shape_block, ifs_m.start())
        if not ifs_block:
            continue

        # coordIndex
        ci_m = re.search(r'coordIndex\s*\[', ifs_block)
        if not ci_m:
            continue
        ci_content = _extract_bracket_content(ifs_block, ci_m.start())
        indices = _parse_ints(ci_content)
        if not indices:
            continue

        # Coordinate point
        pt_m = re.search(r'\bpoint\s*\[', ifs_block)
        if not pt_m:
            # sometimes Coordinate { point [...] } is referenced outside ifs_block
            # fall back: search a window before this Shape in the full text
            shape_abs = shape_m.start()
            window = text[max(0, shape_abs - 20000): shape_abs + len(shape_block) + 1000]
            pt_m2 = re.search(r'\bpoint\s*\[', window)
            if not pt_m2:
                continue
            pt_content = _extract_bracket_content(window, pt_m2.start())
        else:
            pt_content = _extract_bracket_content(ifs_block, pt_m.start())

        floats = _parse_floats(pt_content)
        if floats.size < 9 or floats.size % 3 != 0:
            continue
        verts = floats.reshape(-1, 3)

        faces = _faces_from_index(indices)
        if len(faces) == 0:
            continue

        valid = np.all((faces >= 0) & (faces < len(verts)), axis=1)
        faces = faces[valid]
        if len(faces) == 0:
            continue

        mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)

        # ─ material / colour ─
        color_rgba = [200, 200, 200, 255]  # default grey
        app_m = re.search(r'\bAppearance\s*\{', shape_block)
        if app_m:
            app_block = _extract_brace_block(shape_block, app_m.start())
            mat_m = re.search(r'\bMaterial\s*\{', app_block)
            if mat_m:
                mat_block = _extract_brace_block(app_block, mat_m.start())
                rgb = _parse_diffuse_color(mat_block)
                if rgb:
                    transp = _parse_transparency(mat_block)
                    alpha = max(0, min(255, int((1.0 - transp) * 255)))
                    color_rgba = [
                        int(rgb[0] * 255),
                        int(rgb[1] * 255),
                        int(rgb[2] * 255),
                        alpha,
                    ]

        mesh.visual.face_colors = color_rgba
        meshes.append(mesh)
        logger.info("  Shape: %d verts, %d faces  color=(%d,%d,%d,%d)",
                    len(verts), len(faces), *color_rgba)

    if not meshes:
        raise ValueError(
            "No IndexedFaceSet geometry found in the WRL file."
        )

    combined = trimesh.util.concatenate(meshes)
    logger.info("Total: %d faces, %d vertices, %d shapes",
                len(combined.faces), len(combined.vertices), len(meshes))
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
                logger.warning("Simplification failed (%s) — using original mesh", exc)
                return mesh
    logger.warning("No simplification method available — using original mesh")
    return mesh


def _load_and_simplify(src: Path, max_faces: int):
    mesh = _parse_vrml(src)
    return _simplify(mesh, max_faces)


def _to_bytes(out, fmt: str) -> bytes:
    if isinstance(out, str):
        return out.encode('utf-8')
    return bytes(out)


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

    max_faces = int(request.form.get("max_faces", 80000))
    max_faces = max(5000, min(max_faces, 2_000_000))

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            f.save(tmp)

        mesh = _load_and_simplify(tmp_path, max_faces)

        raw = mesh.export(file_type=out_format)
        out_bytes = _to_bytes(raw, out_format)
        logger.info("Output: %.2f MB  format=%s", len(out_bytes) / 1e6, out_format)

        mime = {
            "glb": "model/gltf-binary",
            "obj": "text/plain",
            "stl": "application/octet-stream",
        }[out_format]

        stem = Path(f.filename).stem
        return send_file(
            io.BytesIO(out_bytes),
            mimetype=mime,
            as_attachment=True,
            download_name=f"{stem}.{out_format}",
        )

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
