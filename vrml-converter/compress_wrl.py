"""
WRL compressor — reduces VRML file size without losing geometry or structure.
Techniques (in order):
  1. Strip comments
  2. Remove blank lines
  3. Reduce float precision (6 → 4 decimal places)
  4. Compact whitespace

Usage:
  python compress_wrl.py input.wrl output.wrl
  python compress_wrl.py input.wrl          # saves as input_compressed.wrl
"""
import re
import sys
import os
from pathlib import Path

_FLOAT_RE = re.compile(
    r'(?<![a-zA-Z0-9_])(-?\d+\.\d{5,})(?:[eE][+-]?\d+)?'
)

def compress_wrl(src_path, dst_path=None):
    src = Path(src_path)
    if dst_path is None:
        dst = src.with_stem(src.stem + '_compressed')
    else:
        dst = Path(dst_path)

    orig_size = src.stat().st_size
    print(f"Input:  {src} ({orig_size/1e6:.1f} MB)")

    print("Reading...")
    text = src.read_bytes().decode('latin-1')

    # Step 1: strip # comments (but keep the first line #VRML header)
    print("Stripping comments...")
    lines = text.split('\n')
    out_lines = []
    for i, line in enumerate(lines):
        if i == 0:
            out_lines.append(line)
            continue
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        # Remove inline comments
        idx = line.find('#')
        if idx != -1:
            line = line[:idx]
        out_lines.append(line)
    text = '\n'.join(out_lines)

    # Step 2: remove blank lines
    print("Removing blank lines...")
    text = re.sub(r'\n\s*\n', '\n', text)

    # Step 3: reduce float precision (keep 4 decimal places)
    print("Reducing float precision...")
    def _round_float(m):
        try:
            val = float(m.group(0))
            # Format with 4 decimal places, strip trailing zeros
            s = f'{val:.4f}'.rstrip('0').rstrip('.')
            return s if s else '0'
        except Exception:
            return m.group(0)
    text = _FLOAT_RE.sub(_round_float, text)

    # Step 4: compact whitespace inside [ ] arrays (coord/index data)
    # Replace sequences of spaces/tabs with single space
    print("Compacting whitespace...")
    text = re.sub(r'[ \t]+', ' ', text)
    # Remove spaces around brackets and braces
    text = re.sub(r' *([\[\]{},]) *', r'\1', text)
    # But keep space after node keywords (before {)
    text = re.sub(r'\b(\w+)\{', r'\1 {', text)

    print("Writing...")
    out_bytes = text.encode('latin-1', errors='replace')
    dst.write_bytes(out_bytes)

    new_size = dst.stat().st_size
    ratio = (1 - new_size / orig_size) * 100
    print(f"Output: {dst} ({new_size/1e6:.1f} MB)")
    print(f"Saved:  {ratio:.1f}% reduction ({orig_size/1e6:.1f} MB → {new_size/1e6:.1f} MB)")

    if new_size < 500_000_000:
        print("✓ Under 500 MB — ready for external converter!")
    else:
        print(f"Still {new_size/1e6:.0f} MB. Try reducing precision further or splitting the file.")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python compress_wrl.py input.wrl [output.wrl]")
        sys.exit(1)
    compress_wrl(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
