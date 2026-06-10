"""
WRL Diagnostic Script
Run: python diagnose.py your_file.wrl
Outputs: diagnose_report.txt

Tells us exactly what node types are used, what the largest IFS blocks look like,
and what structure surrounds roof/door-sized geometry.
"""

import re
import sys
import collections
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose.py yourfile.wrl")
        sys.exit(1)

    src = Path(sys.argv[1])
    print(f"Reading {src.name} ({src.stat().st_size/1e6:.1f} MB) ...")
    text = src.read_text(encoding='utf-8', errors='replace')
    print("Building indexes ...")

    # Build brace index
    brace_idx = {}
    stack = []
    for m in re.finditer(r'[{}]', text):
        if m.group() == '{': stack.append(m.start())
        elif stack: brace_idx[stack.pop()] = m.start()

    # Build bracket index
    bracket_idx = {}
    stack2 = []
    for m in re.finditer(r'[\[\]]', text):
        if m.group() == '[': stack2.append(m.start())
        elif stack2: bracket_idx[stack2.pop()] = m.start()

    print("Scanning nodes ...")

    # Count all node types
    node_counts = collections.Counter()
    for m in re.finditer(r'\b([A-Z][A-Za-z]+)\s*\{', text):
        node_counts[m.group(1)] += 1

    # Find all IndexedFaceSet blocks and measure their size
    ifs_blocks = []  # (pos, n_verts, n_indices, context_before)
    for m in re.finditer(r'\bIndexedFaceSet\s*\{', text):
        bp = text.find('{', m.start())
        if bp == -1: continue
        ce = brace_idx.get(bp)
        if ce is None: continue
        cs = bp + 1

        # count points
        pt_m = re.search(r'\bpoint\s*\[', text[cs:ce])
        n_verts = 0
        if pt_m:
            abs_pt = cs + pt_m.start()
            pk = text.find('[', abs_pt)
            pk_close = bracket_idx.get(pk)
            if pk_close:
                n_verts = text[pk+1:pk_close].count(',')

        # count coordIndex entries
        ci_m = re.search(r'\bcoordIndex\s*\[', text[cs:ce])
        n_idx = 0
        if ci_m:
            abs_ci = cs + ci_m.start()
            ck = text.find('[', abs_ci)
            ck_close = bracket_idx.get(ck)
            if ck_close:
                n_idx = text[ck+1:ck_close].count(',')

        # 300 chars before IFS for context
        ctx = text[max(0, m.start()-300):m.start()].replace('\n',' ').strip()

        ifs_blocks.append((m.start(), n_verts, n_idx, ctx))

    # Sort by vertex count descending
    ifs_blocks.sort(key=lambda x: x[1], reverse=True)

    # Find all DEF names
    def_nodes = []
    for m in re.finditer(r'\bDEF\s+(\S+)\s+(\w+)\s*\{', text):
        def_nodes.append((m.group(2), m.group(1), m.start()))

    # Find bare USE statements
    bare_use = re.findall(r'(?:^|\n)\s*USE\s+(\S+)', text)

    # Unique node types in order of appearance
    first_seen = {}
    for m in re.finditer(r'\b([A-Z][A-Za-z]+)\s*\{', text):
        if m.group(1) not in first_seen:
            first_seen[m.group(1)] = m.start()

    # Write report
    out = Path('diagnose_report.txt')
    with out.open('w', encoding='utf-8') as f:
        f.write(f"=== WRL DIAGNOSTIC: {src.name} ===\n\n")

        f.write("--- ALL NODE TYPES (count) ---\n")
        for node, cnt in node_counts.most_common():
            f.write(f"  {node:40s} {cnt}\n")
        f.write("\n")

        f.write("--- TOP 30 LARGEST IndexedFaceSet BLOCKS ---\n")
        f.write("  (sorted by vertex count — large = roof/door/body panels)\n\n")
        for pos, nv, ni, ctx in ifs_blocks[:30]:
            f.write(f"  pos={pos:10d}  verts~{nv:6d}  indices~{ni:7d}\n")
            f.write(f"  CONTEXT: ...{ctx[-200:]}\n")
            f.write("\n")

        f.write("--- DEF NODES (type, name) first 100 ---\n")
        for ntype, name, pos in def_nodes[:100]:
            f.write(f"  {ntype:30s}  DEF {name}  @ pos {pos}\n")
        f.write("\n")

        f.write(f"--- BARE USE STATEMENTS (first 50 of {len(bare_use)}) ---\n")
        for u in bare_use[:50]:
            f.write(f"  USE {u}\n")
        f.write("\n")

        f.write("--- RAW SAMPLE: 2000 chars around LARGEST IFS ---\n")
        if ifs_blocks:
            big_pos = ifs_blocks[0][0]
            sample = text[max(0, big_pos-500): big_pos+1500]
            f.write(sample)
            f.write("\n\n")

        f.write("--- RAW SAMPLE: 2nd LARGEST IFS ---\n")
        if len(ifs_blocks) > 1:
            big_pos = ifs_blocks[1][0]
            sample = text[max(0, big_pos-500): big_pos+1500]
            f.write(sample)
            f.write("\n")

    print(f"\nReport written to: {out.resolve()}")
    print(f"Total IFS blocks found: {len(ifs_blocks)}")
    if ifs_blocks:
        print(f"Largest IFS: ~{ifs_blocks[0][1]} verts at pos {ifs_blocks[0][0]}")
    print("\nOpen diagnose_report.txt and share its contents.")

if __name__ == '__main__':
    main()
