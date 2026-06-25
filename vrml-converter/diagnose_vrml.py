"""
Quick VRML diagnostic — runs in seconds, no full parse needed.
Usage: python diagnose_vrml.py yourfile.wrl
"""
import re
import sys

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python diagnose_vrml.py yourfile.wrl")
        sys.exit(1)

    print(f"Reading first 2MB of {path} ...")
    with open(path, 'rb') as f:
        raw = f.read(2_000_000)
    text = raw.decode('latin-1', errors='replace')

    # Header
    header = text[:200].replace('\r\n', '\n')
    print(f"\n--- FILE HEADER ---\n{header[:200]}\n")

    # First coordIndex occurrence
    ci_re = re.compile(r'\bcoordIndex\s*(\S)', re.IGNORECASE)
    m = ci_re.search(text)
    if m:
        snippet = text[m.start():m.start()+80].replace('\n','\\n')
        print(f"First 'coordIndex' at offset {m.start()}: {snippet!r}")
    else:
        print("WARNING: No 'coordIndex' found in first 2MB of file!")

    # First IndexedFaceSet body
    ifs_re = re.compile(r'\bIndexedFaceSet\s*\{')
    m2 = ifs_re.search(text)
    if m2:
        body_start = m2.end()
        # find matching }
        depth = 1
        pos = body_start
        while pos < len(text) and depth > 0:
            if text[pos] == '{': depth += 1
            elif text[pos] == '}': depth -= 1
            pos += 1
        body_end = pos - 1
        body = text[body_start:min(body_start+500, body_end)]
        print(f"\n--- First IndexedFaceSet body (offset {m2.start()}) ---")
        print(repr(body[:500]))
        print(f"Body size: {body_end - body_start} chars")
        has_coord = bool(re.search(r'\bcoord\b', body))
        has_ci = bool(re.search(r'\bcoordIndex\b', body))
        has_point = bool(re.search(r'\bpoint\b', body))
        print(f"  has 'coord': {has_coord}")
        print(f"  has 'coordIndex': {has_ci}")
        print(f"  has 'point': {has_point}")
    else:
        print("WARNING: No IndexedFaceSet found in first 2MB!")

    # Count IFS nodes in sample
    n_ifs = len(ifs_re.findall(text))
    print(f"\nIndexedFaceSet nodes in first 2MB: {n_ifs}")

    # First Coordinate3 node
    c3_re = re.compile(r'\bCoordinate3\s*\{')
    m3 = c3_re.search(text)
    if m3:
        snippet = text[m3.start():m3.start()+100].replace('\n','\\n')
        print(f"\nFirst Coordinate3 at offset {m3.start()}: {snippet!r}")
    else:
        print("\nNo Coordinate3 in first 2MB")

    # First Coordinate node (VRML2)
    c_re = re.compile(r'\bCoordinate\s*\{')
    m4 = c_re.search(text)
    if m4:
        snippet = text[m4.start():m4.start()+100].replace('\n','\\n')
        print(f"First Coordinate at offset {m4.start()}: {snippet!r}")
    else:
        print("No Coordinate node in first 2MB")

    # Check if VRML 1 or 2
    if '#VRML V1.0' in text[:100]:
        print("\nFile type: VRML 1.0")
    elif '#VRML V2.0' in text[:100]:
        print("\nFile type: VRML 2.0")
    else:
        print("\nFile type: unknown (no standard header found)")

    print("\nDone. Send this output to diagnose the geometry issue.")

if __name__ == '__main__':
    main()
