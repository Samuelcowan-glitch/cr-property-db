import os, glob

template_dir = r'C:\Users\SamuelC\property-db\templates'
files = glob.glob(os.path.join(template_dir, '**', '*.html'), recursive=True)

fixed = 0
for fpath in files:
    with open(fpath, 'rb') as f:
        raw = f.read()
    try:
        txt = raw.decode('latin-1').encode('latin-1').decode('utf-8')
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(txt)
        fixed += 1
        print(f'Fixed: {os.path.relpath(fpath, template_dir)}')
    except Exception as e:
        pass  # file is already clean UTF-8

print(f'Done. Re-encoded {fixed} files.')
