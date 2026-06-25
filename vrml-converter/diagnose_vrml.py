"""
Deep VRML diagnostic — simulates _parse_geo on first IFS bodies.
Usage: python diagnose_vrml.py yourfile.wrl
Runs in seconds.
"""
import re
import sys
import numpy as np

_NUM_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_CI_RE  = re.compile(r'\bcoordIndex\s*\[')
_COORD_FIELD_RE = re.compile(r'\bcoord\s+(?:DEF\s+\S+\s+)?Coordinate\s*\{')
_PT_RE  = re.compile(r'\bpoint\s*\[')

def find_matching_brace(text, start):
    depth = 0
    i = start
    while i < len(text):
        if text[i] == '{': depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def find_matching_bracket(text, start):
    depth = 0
    i = start
    while i < len(text):
        if text[i] == '[': depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def parse_floats(text, p0, p1):
    chunk = text[p0:p1]
    vals = _NUM_RE.findall(chunk)
    if not vals:
        return None
    return np.array([float(v) for v in vals], dtype=np.float64)

def parse_face_indices(text, p0, p1):
    chunk = text[p0:p1]
    vals = _NUM_RE.findall(chunk)
    if not vals:
        return np.empty(0, dtype=np.int32)
    return np.array([int(float(v)) for v in vals], dtype=np.int32)

def build_faces(indices):
    faces = []
    fan = []
    for idx in indices:
        if idx < 0:
            if len(fan) >= 3:
                v0 = fan[0]
                for j in range(1, len(fan) - 1):
                    faces.append((v0, fan[j], fan[j+1]))
            fan = []
        else:
            fan.append(int(idx))
    if len(fan) >= 3:
        v0 = fan[0]
        for j in range(1, len(fan) - 1):
            faces.append((v0, fan[j], fan[j+1]))
    return np.array(faces, dtype=np.int32) if faces else np.empty((0,3), dtype=np.int32)

def simulate_parse_geo(text, ncs, nce, body_label):
    """Simulate what _parse_geo does and report each step."""
    print(f"\n  {body_label} body [{ncs}:{nce}], size={nce-ncs}")
    body = text[ncs:nce]

    # Step 1: find coordIndex
    ci_m = _CI_RE.search(body)
    if ci_m is None:
        print("  FAIL: coordIndex not found in body")
        return False
    print(f"  OK: coordIndex found at body[{ci_m.start()}]")

    # Step 2: find [ ] for coordIndex
    ci_abs = ncs + ci_m.start()
    bracket_open = text.find('[', ci_abs)
    if bracket_open == -1 or bracket_open >= nce:
        print(f"  FAIL: [ not found after coordIndex (abs pos {ci_abs})")
        return False
    bracket_close = find_matching_bracket(text, bracket_open)
    if bracket_close == -1 or bracket_close > nce:
        print(f"  FAIL: ] not found for coordIndex [")
        return False
    print(f"  OK: coordIndex[{bracket_open}:{bracket_close}], len={bracket_close-bracket_open}")

    # Step 3: parse indices
    indices = parse_face_indices(text, bracket_open+1, bracket_close)
    print(f"  OK: parsed {len(indices)} raw indices")
    if len(indices) == 0:
        print("  FAIL: no indices parsed")
        return False

    # Step 4: build faces
    faces = build_faces(indices)
    print(f"  OK: built {len(faces)} triangles")
    if len(faces) == 0:
        print("  FAIL: no faces built")
        return False

    # Step 5: find inline coord
    cm = _COORD_FIELD_RE.search(body)
    if cm is None:
        print("  INFO: no inline 'coord Coordinate {' found — needs parent_coord")
        return True  # Not a failure, but no inline coord

    coord_abs = ncs + cm.start()
    brace_open = text.find('{', coord_abs)
    if brace_open == -1 or brace_open >= nce:
        print(f"  FAIL: {{ not found for coord Coordinate")
        return False
    brace_close = find_matching_brace(text, brace_open)
    if brace_close == -1 or brace_close > nce:
        print(f"  FAIL: }} not found for coord Coordinate (searched from {brace_open})")
        return False
    print(f"  OK: Coordinate body [{brace_open+1}:{brace_close}], len={brace_close-brace_open}")

    inner = text[brace_open+1:brace_close]
    pt_m = _PT_RE.search(inner)
    if pt_m is None:
        print(f"  FAIL: 'point [' not found inside Coordinate body")
        return False
    pt_abs = brace_open + 1 + pt_m.start()
    pt_bracket = text.find('[', pt_abs)
    if pt_bracket == -1 or pt_bracket >= brace_close:
        print(f"  FAIL: [ not found after 'point'")
        return False
    pt_bracket_close = find_matching_bracket(text, pt_bracket)
    if pt_bracket_close == -1 or pt_bracket_close > brace_close:
        print(f"  FAIL: ] not found for point [")
        return False
    print(f"  OK: point[{pt_bracket+1}:{pt_bracket_close}], len={pt_bracket_close-pt_bracket}")

    floats = parse_floats(text, pt_bracket+1, pt_bracket_close)
    if floats is None or len(floats) < 3 or len(floats) % 3 != 0:
        print(f"  FAIL: bad float count: {len(floats) if floats is not None else 0}")
        return False
    coord = floats.reshape(-1, 3)
    print(f"  OK: coord has {len(coord)} points")

    # Step 6: validate face indices against coord
    valid = np.all((faces >= 0) & (faces < len(coord)), axis=1)
    valid_faces = faces[valid]
    print(f"  OK: {len(valid_faces)}/{len(faces)} faces pass index validation (coord size={len(coord)})")
    if len(valid_faces) == 0:
        print(f"  FAIL: ALL faces out of range! Max index={faces.max() if len(faces)>0 else 'N/A'}, coord={len(coord)}")
        return False

    print(f"  SUCCESS: would produce {len(valid_faces)} faces, {len(coord)} verts")
    return True


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python diagnose_vrml.py yourfile.wrl")
        sys.exit(1)

    print(f"Reading first 10MB of {path} ...")
    with open(path, 'rb') as f:
        raw = f.read(10_000_000)
    text = raw.decode('latin-1', errors='replace')
    print(f"Read {len(text)} chars. File: {'VRML 2.0' if '#VRML V2.0' in text[:100] else 'VRML 1.0' if '#VRML V1.0' in text[:100] else 'unknown'}")

    # Find all IFS in first 10MB
    ifs_re = re.compile(r'\bIndexedFaceSet\s*\{')
    matches = list(ifs_re.finditer(text))
    print(f"\nFound {len(matches)} IFS nodes in first 10MB")

    # Simulate _parse_geo on first 10
    print("\n--- Simulating _parse_geo on first 10 IFS bodies ---")
    ok = 0
    fail = 0
    for i, m in enumerate(matches[:10]):
        ifs_open = m.end() - 1
        ifs_close = find_matching_brace(text, ifs_open)
        if ifs_close == -1:
            print(f"\n  IFS #{i+1}: can't find closing brace")
            fail += 1
            continue
        result = simulate_parse_geo(text, ifs_open+1, ifs_close, f"IFS #{i+1}")
        if result:
            ok += 1
        else:
            fail += 1

    print(f"\nResult: {ok} OK, {fail} FAILED out of first 10 IFS bodies")

    # Check if Shape wraps the IFS
    print("\n--- Checking Shape → IFS nesting ---")
    shape_re = re.compile(r'\bShape\s*\{')
    sm = shape_re.search(text)
    if sm:
        shape_open = sm.end() - 1
        shape_close = find_matching_brace(text, shape_open)
        shape_body = text[shape_open+1:shape_close] if shape_close != -1 else ''
        has_geo = bool(re.search(r'\bgeometry\b', shape_body[:500]))
        has_ifs = bool(ifs_re.search(shape_body))
        has_app = bool(re.search(r'\bappearance\b', shape_body[:500]))
        print(f"First Shape at {sm.start()}, body_size={shape_close-shape_open if shape_close!=-1 else '?'}")
        print(f"  has 'geometry': {has_geo}")
        print(f"  has IndexedFaceSet: {has_ifs}")
        print(f"  has 'appearance': {has_app}")
        print(f"  first 300 chars: {shape_body[:300].replace(chr(13),'').replace(chr(10),' ')!r}")
    else:
        print("No Shape node found in first 10MB")

    print("\nDone.")

if __name__ == '__main__':
    main()
