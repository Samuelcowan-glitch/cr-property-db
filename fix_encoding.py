import os

files = [
    r'C:\Users\SamuelC\property-db\templates\dashboard.html',
    r'C:\Users\SamuelC\property-db\templates\crm\enquiries_list.html',
    r'C:\Users\SamuelC\property-db\templates\crm\enquiry_detail.html',
]

for fpath in files:
    if not os.path.exists(fpath):
        print(f'SKIP: {fpath}')
        continue
    with open(fpath, 'r', encoding='latin-1') as f:
        content = f.read()
    try:
        content = content.encode('latin-1').decode('utf-8')
    except Exception as e:
        print(f'Re-encode error on {fpath}: {e}')
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'Fixed: {fpath}')

print('Done')
