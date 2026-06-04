from pathlib import Path
import unicodedata

replacements = {
    '\u2013': '-',
    '\u2014': '-',
    '\u2018': "'",
    '\u2019': "'",
    '\u201c': '"',
    '\u201d': '"',
    '\u2026': '...',
    '\u00a0': ' ',
    '\u2010': '-',
    '\u2011': '-',
}

root = Path('.')
files = list(root.rglob('*.py')) + list(root.rglob('*.json')) + list(root.rglob('*.md'))
updated = []
for path in files:
    data = path.read_bytes()
    if not any(b > 127 for b in data):
        continue
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        text = data.decode('cp1252', errors='replace')
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = unicodedata.normalize('NFC', text)
    path.write_text(text, encoding='utf-8', newline='\n')
    updated.append(path)

print(f'Updated {len(updated)} files')
for path in updated:
    print(path)
