"""
VRML / WRL → GLB / OBJ Converter
Standalone local web app — runs on CPU, no GPU needed.
Usage:
    pip install flask trimesh[easy] numpy
    python app.py
Then open http://localhost:5555 in your browser.
"""

import io
import logging
import os
import tempfile
import traceback
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, send_file

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # allow up to 4 GB

# ── HTML page ──────────────────────────────────────────────────────────────────

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
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2rem;
  }
  .card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 16px;
    padding: 2.5rem;
    width: 100%;
    max-width: 560px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }
  h1 { font-size: 1.6rem; color: #7dd3fc; margin-bottom: .25rem; }
  .sub { color: #94a3b8; font-size: .9rem; margin-bottom: 2rem; }

  label { display: block; font-size: .85rem; color: #94a3b8; margin-bottom: .4rem; }

  .drop-zone {
    border: 2px dashed #334155;
    border-radius: 12px;
    padding: 2.5rem;
    text-align: center;
    cursor: pointer;
    transition: border-color .2s, background .2s;
    margin-bottom: 1.5rem;
  }
  .drop-zone:hover, .drop-zone.dragover {
    border-color: #7dd3fc;
    background: #0f172a;
  }
  .drop-zone input[type=file] { display: none; }
  .drop-zone .icon { font-size: 2.5rem; margin-bottom: .5rem; }
  .drop-zone .hint { color: #64748b; font-size: .85rem; margin-top: .4rem; }
  .drop-zone .chosen { color: #7dd3fc; font-weight: 600; margin-top: .6rem; font-size: .95rem; }

  .row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
  .field { flex: 1; }
  select, input[type=number] {
    width: 100%;
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    color: #e2e8f0;
    padding: .55rem .75rem;
    font-size: .9rem;
  }
  select:focus, input[type=number]:focus {
    outline: none;
    border-color: #7dd3fc;
  }

  button {
    width: 100%;
    background: #0284c7;
    color: #fff;
    border: none;
    border-radius: 10px;
    padding: .85rem;
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
    transition: background .2s;
  }
  button:hover { background: #0369a1; }
  button:disabled { background: #334155; color: #64748b; cursor: not-allowed; }

  .progress-wrap { margin-top: 1.5rem; display: none; }
  .progress-bar {
    background: #1e3a5f;
    border-radius: 8px;
    height: 10px;
    overflow: hidden;
    margin-bottom: .5rem;
  }
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #0284c7, #7dd3fc);
    border-radius: 8px;
    width: 0%;
    transition: width .3s ease;
  }
  .progress-label { font-size: .85rem; color: #94a3b8; text-align: center; }

  .result {
    margin-top: 1.5rem;
    padding: 1rem 1.25rem;
    border-radius: 10px;
    font-size: .9rem;
    display: none;
  }
  .result.ok  { background: #052e16; border: 1px solid #16a34a; color: #86efac; }
  .result.err { background: #2d0a0a; border: 1px solid #dc2626; color: #fca5a5; }

  .stats { margin-top: .6rem; font-size: .82rem; color: #64748b; }
</style>
</head>
<body>
<div class="card">
  <h1>&#127922; VRML / WRL Converter</h1>
  <p class="sub">Runs locally on CPU &mdash; no GPU needed &mdash; converts .wrl to .glb or .obj</p>

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

// Drag-and-drop
const dz = document.getElementById('dropZone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('dragover');
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});

function onFileChosen(input) {
  if (input.files[0]) setFile(input.files[0]);
}

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
  const pw  = document.getElementById('progressWrap');
  const pf  = document.getElementById('progressFill');
  const pl  = document.getElementById('progressLabel');
  const rb  = document.getElementById('resultBox');

  btn.disabled = true;
  rb.style.display = 'none';
  pw.style.display = 'block';
  pf.style.width = '5%';
  pl.textContent = 'Uploading file…';

  const fmt      = document.getElementById('outFmt').value;
  const maxFaces = parseInt(document.getElementById('maxFaces').value) || 80000;
  const form     = new FormData();
  form.append('file', chosenFile);
  form.append('out_format', fmt);
  form.append('max_faces', maxFaces);

  // Simulate progress while waiting (XHR gives real upload progress)
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/convert');
  xhr.responseType = 'blob';

  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 50);  // upload = 0-50%
      pf.style.width = pct + '%';
      pl.textContent = 'Uploading… ' + pct + '%';
    }
  };

  // Fake server-side progress 50→95%
  let fakeP = 50;
  const ticker = setInterval(() => {
    fakeP = Math.min(fakeP + (fakeP < 70 ? 2 : fakeP < 88 ? 0.8 : 0.2), 94);
    pf.style.width = fakeP + '%';
    pl.textContent = 'Converting on server… ' + Math.round(fakeP) + '%';
  }, 600);

  xhr.onload = () => {
    clearInterval(ticker);
    pf.style.width = '100%';
    pl.textContent = 'Done!';
    btn.disabled = false;

    if (xhr.status === 200) {
      const url  = URL.createObjectURL(xhr.response);
      const base = chosenFile.name.replace(/\\.[^.]+$/, '');
      const a    = document.createElement('a');
      a.href     = url;
      a.download = base + '.' + fmt;
      a.click();
      URL.revokeObjectURL(url);

      rb.className = 'result ok';
      rb.style.display = 'block';
      rb.innerHTML = '&#9989; Conversion complete! Download started.<br/>'
        + '<span class="stats">Output size: ' + formatBytes(xhr.response.size) + '</span>';
    } else {
      xhr.response.text().then(txt => {
        let msg = 'Conversion failed.';
        try { msg = JSON.parse(txt).detail || msg; } catch(_) {}
        rb.className = 'result err';
        rb.style.display = 'block';
        rb.textContent = '✗ ' + msg;
      });
    }
  };

  xhr.onerror = () => {
    clearInterval(ticker);
    btn.disabled = false;
    rb.className = 'result err';
    rb.style.display = 'block';
    rb.textContent = '✗ Network error. Is the server running?';
  };

  xhr.send(form);
}
</script>
</body>
</html>
"""


# ── Conversion logic ───────────────────────────────────────────────────────────

def _load_and_simplify(src: Path, max_faces: int):
    import trimesh

    logger.info("Loading %s  (%.1f MB) …", src.name, src.stat().st_size / 1e6)
    scene = trimesh.load(str(src), force="scene")

    if isinstance(scene, trimesh.Scene):
        meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise ValueError("No renderable geometry found in the WRL file.")
        mesh = trimesh.util.concatenate(meshes)
    elif isinstance(scene, trimesh.Trimesh):
        mesh = scene
    else:
        raise ValueError(f"Unexpected trimesh type: {type(scene)}")

    logger.info("Loaded: %d faces, %d vertices", len(mesh.faces), len(mesh.vertices))

    if len(mesh.faces) > max_faces:
        logger.info("Simplifying to ~%d faces …", max_faces)
        try:
            mesh = mesh.simplify_quadratic_decimation(max_faces)
            logger.info("After simplification: %d faces", len(mesh.faces))
        except Exception as exc:
            logger.warning("Simplification failed (%s) — using original mesh", exc)

    return mesh


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

        out_bytes = mesh.export(file_type=out_format)
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
