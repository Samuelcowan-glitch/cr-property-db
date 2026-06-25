f = r'C:\Users\SamuelC\property-db\templates\projects\listing_form.html'
with open(f, encoding='utf-8') as fh:
    txt = fh.read()

# Fix: rename 'items' key to 'opts' to avoid clash with dict.items() method
txt = txt.replace("'items':", "'opts':")
txt = txt.replace("{% for o in opt.items %}", "{% for o in opt['opts'] %}")
txt = txt.replace("opt.group", "opt['group']")

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(txt)
print('Fixed')
