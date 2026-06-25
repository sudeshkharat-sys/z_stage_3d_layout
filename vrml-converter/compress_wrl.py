"""
WRL compressor — processes line by line to avoid OOM on large files.
Reduces float precision and strips comments/whitespace.

Usage:
  python compress_wrl.py input.wrl output.wrl
  python compress_wrl.py input.wrl          # saves as input_compressed.wrl
"""
import re
import sys
from pathlib import Path

_FLOAT_RE = re.compile(r'-?\d+\.\d{3,}(?:[eE][+-]?\d+)?')

def _round_float(m):
    try:
        val = float(m.group(0))
        s = f'{val:.2f}'.rstrip('0').rstrip('.')
        return s if s else '0'
    except Exception:
        return m.group(0)

def compress_wrl(src_path, dst_path=None):
    src = Path(src_path)
    dst = Path(dst_path) if dst_path else src.with_stem(src.stem + '_compressed')

    orig_size = src.stat().st_size
    print(f"Input:  {src} ({orig_size/1e6:.1f} MB)")
    print("Processing line by line (low memory)...")

    written = 0
    with open(str(src), 'rb') as fin, open(str(dst), 'wb') as fout:
        for i, raw_line in enumerate(fin):
            line = raw_line.decode('latin-1')

            # Keep VRML header on line 0
            if i == 0:
                fout.write(line.encode('latin-1'))
                continue

            # Strip full-line comments
            stripped = line.lstrip()
            if stripped.startswith('#'):
                continue

            # Remove inline comments
            ci = line.find('#')
            if ci != -1:
                line = line[:ci]

            # Skip blank lines
            if not line.strip():
                continue

            # Reduce float precision
            line = _FLOAT_RE.sub(_round_float, line)

            # Compact whitespace (tabs/multiple spaces → single space)
            line = re.sub(r'[ \t]+', ' ', line).strip()

            if not line:
                continue

            fout.write((line + '\n').encode('latin-1'))
            written += 1
            if written % 1_000_000 == 0:
                print(f"  {written/1e6:.0f}M lines written...")

    new_size = dst.stat().st_size
    ratio = (1 - new_size / orig_size) * 100
    print(f"Output: {dst} ({new_size/1e6:.1f} MB)")
    print(f"Saved:  {ratio:.1f}% reduction  ({orig_size/1e6:.1f} MB → {new_size/1e6:.1f} MB)")

    if new_size < 500_000_000:
        print("Under 500 MB — ready for external converter!")
    else:
        print(f"Still {new_size/1e6:.0f} MB. May need further reduction.")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python compress_wrl.py input.wrl [output.wrl]")
        sys.exit(1)
    compress_wrl(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
