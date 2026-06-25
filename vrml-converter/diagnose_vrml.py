"""
Quick VRML diagnostic — runs in seconds.
Usage: python diagnose_vrml.py yourfile.wrl
"""
import re
import sys

def find_matching_brace(text, start):
    """Find the closing } for opening { at position start."""
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
    """Find the closing ] for opening [ at position start."""
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

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python diagnose_vrml.py yourfile.wrl")
        sys.exit(1)

    print(f"Reading first 5MB of {path} ...")
    with open(path, 'rb') as f:
        raw = f.read(5_000_000)
    text = raw.decode('latin-1', errors='replace')

    # Header
    print(f"\n--- FILE HEADER ---")
    print(text[:150].strip())

    if '#VRML V1.0' in text[:100]:
        print("\nFile type: VRML 1.0")
    elif '#VRML V2.0' in text[:100]:
        print("\nFile type: VRML 2.0")
    else:
        print("\nFile type: UNKNOWN")

    ifs_re = re.compile(r'\bIndexedFaceSet\s*\{')

    # Find and analyze first 5 IFS bodies
    print("\n--- IndexedFaceSet bodies ---")
    found = 0
    search_start = 0
    while found < 5:
        m = ifs_re.search(text, search_start)
        if not m:
            break
        ifs_open = m.end() - 1  # position of {
        ifs_close = find_matching_brace(text, ifs_open)
        if ifs_close == -1:
            print(f"IFS #{found+1} at {m.start()}: can't find closing brace")
            search_start = m.end()
            continue

        body = text[ifs_open+1:ifs_close]
        body_len = len(body)

        has_coord_inline = bool(re.search(r'\bcoord\s+(?:DEF\s+\S+\s+)?Coordinate\s*\{', body))
        has_coord_use = bool(re.search(r'\bcoord\s+USE\s+', body))
        has_ci = bool(re.search(r'\bcoordIndex\s*\[', body))
        has_point = bool(re.search(r'\bpoint\s*\[', body))

        # Count points if inline
        n_points = 0
        pt_m = re.search(r'\bpoint\s*\[', body)
        if pt_m:
            pb = ifs_open + 1 + pt_m.start()
            pb_open = text.find('[', pb)
            if pb_open != -1:
                pb_close = find_matching_bracket(text, pb_open)
                if pb_close != -1:
                    pts_text = text[pb_open+1:pb_close]
                    n_points = pts_text.count(',') // 3 + 1  # rough estimate

        # Count face indices
        n_faces = 0
        ci_m = re.search(r'\bcoordIndex\s*\[', body)
        if ci_m:
            ci_pos = ifs_open + 1 + ci_m.start()
            ci_open = text.find('[', ci_pos)
            if ci_open != -1 and ci_open < ifs_close:
                ci_close = find_matching_bracket(text, ci_open)
                if ci_close != -1 and ci_close < ifs_close:
                    ci_text = text[ci_open+1:ci_close]
                    n_faces = ci_text.count('-1')

        print(f"\nIFS #{found+1} at offset {m.start()}, body_size={body_len}")
        print(f"  coord inline: {has_coord_inline}, coord USE: {has_coord_use}, point[]: {has_point}")
        print(f"  coordIndex[]: {has_ci}")
        print(f"  approx points: {n_points}, approx faces: {n_faces}")
        print(f"  first 150 chars: {body[:150].replace(chr(13),'').replace(chr(10),' ')!r}")

        search_start = ifs_close + 1
        found += 1

    if found == 0:
        print("No IndexedFaceSet found in first 5MB!")

    # Count totals
    total_ifs = len(ifs_re.findall(text))
    total_coord = len(re.findall(r'\bCoordinate\s*\{', text))
    total_coordIndex = len(re.findall(r'\bcoordIndex\s*\[', text))
    print(f"\n--- Counts in first 5MB ---")
    print(f"  IndexedFaceSet: {total_ifs}")
    print(f"  Coordinate nodes: {total_coord}")
    print(f"  coordIndex fields: {total_coordIndex}")
    print(f"  (coordIndex == IFS ratio): {total_coordIndex}/{total_ifs} = {total_coordIndex/max(1,total_ifs):.2f}")

    # Check for USE/DEF patterns
    n_def_ifs = len(re.findall(r'\bDEF\s+\S+\s+IndexedFaceSet\s*\{', text))
    n_use = len(re.findall(r'(?<!\w)USE\s+\S+', text))
    print(f"  DEF-named IFS: {n_def_ifs}")
    print(f"  USE references: {n_use}")

    print("\nDone.")

if __name__ == '__main__':
    main()
