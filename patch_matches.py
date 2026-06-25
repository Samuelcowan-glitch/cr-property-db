f = r'C:\Users\SamuelC\property-db\templates\projects\detail.html'
with open(f, 'r', encoding='utf-8') as fh:
    txt = fh.read()

# Find the match score circle section and add register button after it
old = '''              <!-- Match score -->
              <div style="flex-shrink:0;text-align:center">
                <div style="width:32px;height:32px;border-radius:50%;background:{% if score >= 8 %}#1b7a3f{% elif score >= 5 %}#c8601a{% else %}#9aa0a6{% endif %};color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700">{{ score }}</div>
                <div style="font-size:9.5px;color:#9aa0a6;margin-top:2px">score</div>
              </div>
            </div>
          </div>'''

new = '''              <!-- Match score + register -->
              <div style="flex-shrink:0;text-align:center">
                <div style="width:32px;height:32px;border-radius:50%;background:{% if score >= 8 %}#1b7a3f{% elif score >= 5 %}#c8601a{% else %}#9aa0a6{% endif %};color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700">{{ score }}</div>
                <div style="font-size:9.5px;color:#9aa0a6;margin-top:2px">score</div>
              </div>
            </div>
            <!-- Register / status -->
            {% if contact.id in registered_ids %}
            {% set pa = registered_ids[contact.id] %}
            <div style="margin-top:6px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
              <span style="font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:10px;background:#e6f4ea;color:#1b7a3f">&#10003; {{ pa.status }}</span>
              <form method="post" action="{{ url_for('applicant_status', id=pa.id) }}" style="display:flex;gap:4px;align-items:center">
                <select name="status" style="font-size:10.5px;padding:2px 4px;border:1px solid #dde0e4;border-radius:4px">
                  {% for s in ['Active Applicant','Viewing Arranged','Offer Made','Declined','Let/Sold'] %}
                  <option value="{{ s }}" {{ 'selected' if pa.status == s else '' }}>{{ s }}</option>
                  {% endfor %}
                </select>
                <button type="submit" style="font-size:10.5px;padding:2px 6px;background:#1a2e4a;color:#fff;border:none;border-radius:4px;cursor:pointer">Update</button>
              </form>
              <form method="post" action="{{ url_for('applicant_remove', id=pa.id) }}" class="confirm-delete">
                <button type="submit" style="font-size:10.5px;padding:2px 6px;background:none;border:1px solid #fecaca;color:#c62828;border-radius:4px;cursor:pointer">Remove</button>
              </form>
            </div>
            {% else %}
            <div style="margin-top:6px">
              <form method="post" action="{{ url_for('applicant_register', proj_id=project.id, contact_id=contact.id) }}">
                <button type="submit" class="btn btn-sm" style="width:100%;background:#1a2e4a;color:#fff;border:none;font-size:10.5px">+ Register as Applicant</button>
              </form>
            </div>
            {% endif %}
          </div>'''

if old in txt:
    txt = txt.replace(old, new, 1)
    with open(f, 'w', encoding='utf-8') as fh:
        fh.write(txt)
    print('Matches panel updated.')
else:
    print('Pattern not found - checking snippet...')
    idx = txt.find('Match score')
    print(repr(txt[idx:idx+200]))
