f = r'C:\Users\SamuelC\property-db\templates\dashboard.html'

with open(f, 'r', encoding='utf-8', errors='replace') as fh:
    txt = fh.read()

# Replace garbled sequences with clean HTML entities or simple text
txt = txt.replace('Filter contactsâ€¦', 'Filter contacts...')
txt = txt.replace('Filter listingsâ€¦', 'Filter listings...')
txt = txt.replace('Filter enquiriesâ€¦', 'Filter enquiries...')

# Contact row icons - replace garbled with simple labels
txt = txt.replace('<span class="ci">ðŸ›¯</span>', '<span class="ci" style="font-style:normal;color:#9aa0a6;font-size:10px">ORG</span>')
txt = txt.replace('<span class="ci">ðŸ“ž</span>', '<span class="ci" style="font-style:normal;color:#9aa0a6;font-size:10px">T</span>')
txt = txt.replace('<span class="ci">ðŸ“±</span>', '<span class="ci" style="font-style:normal;color:#9aa0a6;font-size:10px">M</span>')
txt = txt.replace('<span class="ci">âï¸</span>', '<span class="ci" style="font-style:normal;color:#9aa0a6;font-size:10px">@</span>')
txt = txt.replace('<span class="ci">ðŸ·ï¸</span>', '<span class="ci" style="font-style:normal;color:#9aa0a6;font-size:10px">TYPE</span>')

# Clean up arrow corruption
txt = txt.replace('View all â†’', 'View all')
txt = txt.replace('Record an enquiryâ†’', 'Record an enquiry')
txt = txt.replace('Add your first contact â†’', 'Add your first contact')

# Simpler: just replace the ci spans with clean text versions
import re
# Replace any remaining garbled ci spans
txt = re.sub(r'<span class="ci">[^\<]{1,20}</span>', '<span class="ci">·</span>', txt)

# Also fix â€" to —
txt = txt.replace('â€“', '—')  # —
txt = txt.replace('Â·', '·')  # ·

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(txt)
print('Done')
