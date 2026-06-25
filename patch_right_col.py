f = r'C:\Users\SamuelC\property-db\templates\projects\detail.html'
with open(f, 'r', encoding='utf-8') as fh:
    txt = fh.read()

# Find Activity Feed right column and replace to end of its closing divs
start = txt.find('  <!-- ════ RIGHT: Activity Feed')
end   = txt.find('</div>\n</div>\n\n\n<!-- ══', start)
if start < 0 or end < 0:
    # Try alternative
    start = txt.find('<!-- ════ RIGHT: Activity Feed')
    end   = txt.find('\n\n\n<!-- ══════', start)

print(f'start={start}, end={end}')
if start > 0 and end > 0:
    old_block = txt[start:end]
    print('Old block last 80 chars:', repr(old_block[-80:]))

    new_right = '''  <!-- ════ RIGHT: Enquiries ════ -->
  <div style="position:sticky;top:68px;display:flex;flex-direction:column;gap:12px">

    <!-- Direct enquiries for this property/project -->
    <div class="card" style="padding:0;overflow:hidden">
      <div style="background:#c8601a;color:#fff;padding:9px 14px;display:flex;align-items:center;justify-content:space-between">
        <span style="font-size:13px;font-weight:700">Direct Enquiries</span>
        <a href="{{ url_for('enquiry_new') }}" style="color:rgba(255,255,255,.8);font-size:11px">+ Add</a>
      </div>
      <div style="max-height:260px;overflow-y:auto">
        {% set direct_enqs = [] %}
        {% if project.property %}
          {% set direct_enqs = project.property.enquiries %}
        {% endif %}
        {% if direct_enqs %}
          {% for e in direct_enqs | sort(attribute='created_at', reverse=True) %}
          <div style="padding:10px 12px;border-bottom:1px solid #f1f3f4">
            {% if e.contact %}
            <div style="font-size:13px;font-weight:600;color:#c8601a;margin-bottom:3px">
              <a href="{{ url_for('contact_detail', id=e.contact.id) }}" style="color:#c8601a">{{ e.contact.full_name }}</a>
            </div>
            {% if e.contact.phone %}
            <div style="font-size:11.5px;color:#5f6368;margin-bottom:2px">{{ e.contact.phone }}</div>
            {% endif %}
            {% if e.contact.email %}
            <div style="font-size:11.5px;color:#5f6368;margin-bottom:2px">{{ e.contact.email }}</div>
            {% endif %}
            {% endif %}
            <div style="font-size:11px;color:#1a2e4a;font-weight:600;margin-top:3px">{{ e.subject }}</div>
            <div style="font-size:10.5px;color:#9aa0a6;margin-top:2px;display:flex;gap:8px">
              <span>{{ e.enquiry_type or 'Enquiry' }}</span>
              <span>{{ e.received_date.strftime('%d %b %Y') if e.received_date else '' }}</span>
              <span style="color:{{ '#1b7a3f' if e.status == 'Open' else '#9aa0a6' }};font-weight:600">{{ e.status }}</span>
            </div>
            {% if e.notes %}
            <div style="font-size:11px;color:#5f6368;margin-top:4px;background:#f8f9fa;padding:5px 8px;border-radius:4px">{{ e.notes[:120] }}{% if e.notes|length > 120 %}...{% endif %}</div>
            {% endif %}
          </div>
          {% endfor %}
        {% else %}
        <div style="padding:20px;text-align:center;color:#9aa0a6;font-size:12px">
          No direct enquiries yet.<br>
          <a href="{{ url_for('enquiry_new') }}" style="color:#c8601a;margin-top:4px;display:inline-block">Record an enquiry</a>
        </div>
        {% endif %}
      </div>
    </div>

    <!-- Matched applicants (similar properties) -->
    <div class="card" style="padding:0;overflow:hidden">
      <div style="background:#1a2e4a;color:#fff;padding:9px 14px;display:flex;align-items:center;justify-content:space-between">
        <span style="font-size:13px;font-weight:700">Matched Applicants</span>
        <span style="font-size:11px;opacity:.75">{{ matches|length if matches else 0 }}</span>
      </div>
      <div style="max-height:320px;overflow-y:auto">
        {% if matches %}
          {% for score, reasons, contact in matches %}
          {% set pa = registered_ids.get(contact.id) if registered_ids else None %}
          <div style="padding:10px 12px;border-bottom:1px solid #f1f3f4">
            <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:6px">
              <div style="flex:1;min-width:0">
                <a href="{{ url_for('contact_detail', id=contact.id) }}" style="font-size:12.5px;font-weight:600;color:#c8601a;display:block">{{ contact.full_name }}</a>
                {% if contact.organisation %}<div style="font-size:11px;color:#5f6368">{{ contact.organisation.name }}</div>{% endif %}
                {% if contact.phone %}<div style="font-size:11.5px;color:#5f6368;margin-top:2px">{{ contact.phone }}</div>{% endif %}
                {% if contact.email %}<div style="font-size:11.5px;color:#5f6368">{{ contact.email }}</div>{% endif %}
                <!-- Requirements chips -->
                <div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:5px">
                  {% if contact.req_area %}<span style="background:#e8f0fe;color:#1565c0;font-size:9.5px;padding:1px 6px;border-radius:8px">{{ contact.req_area }}</span>{% endif %}
                  {% if contact.req_size_max %}<span style="background:#e6f4ea;color:#1b7a3f;font-size:9.5px;padding:1px 6px;border-radius:8px">to {{ '{:,.0f}'.format(contact.req_size_max) }} sq ft</span>{% endif %}
                  {% if contact.req_budget_max %}<span style="background:#fff3e0;color:#e65100;font-size:9.5px;padding:1px 6px;border-radius:8px">&#163;{{ '{:,.0f}'.format(contact.req_budget_max) }} max</span>{% endif %}
                </div>
              </div>
              <!-- Score badge -->
              <div style="flex-shrink:0;width:28px;height:28px;border-radius:50%;background:{{ '#1b7a3f' if score >= 8 else '#c8601a' if score >= 5 else '#9aa0a6' }};color:#fff;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700">{{ score }}</div>
            </div>
            <!-- Register / status -->
            {% if pa %}
            <div style="margin-top:6px;display:flex;align-items:center;gap:6px">
              <span style="font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:8px;background:#e6f4ea;color:#1b7a3f">&#10003; {{ pa.status }}</span>
              <form method="post" action="{{ url_for('applicant_status', id=pa.id) }}" style="display:flex;gap:4px;align-items:center">
                <select name="status" style="font-size:10px;padding:2px 4px;border:1px solid #dde0e4;border-radius:4px">
                  {% for s in ['Active Applicant','Viewing Arranged','Offer Made','Declined','Let/Sold'] %}
                  <option value="{{ s }}" {{ 'selected' if pa.status == s else '' }}>{{ s }}</option>
                  {% endfor %}
                </select>
                <button type="submit" style="font-size:10px;padding:2px 6px;background:#1a2e4a;color:#fff;border:none;border-radius:3px;cursor:pointer">&#10003;</button>
              </form>
            </div>
            {% else %}
            <div style="margin-top:6px">
              <form method="post" action="{{ url_for('applicant_register', proj_id=project.id, contact_id=contact.id) }}">
                <button type="submit" style="width:100%;font-size:10.5px;padding:4px;background:#1a2e4a;color:#fff;border:none;border-radius:4px;cursor:pointer">+ Register as Applicant</button>
              </form>
            </div>
            {% endif %}
          </div>
          {% endfor %}
        {% else %}
        <div style="padding:20px;text-align:center;color:#9aa0a6;font-size:12px">
          No matching applicants.<br>
          <a href="{{ url_for('contact_new') }}" style="color:#c8601a;margin-top:4px;display:inline-block">Add a contact with requirements</a>
        </div>
        {% endif %}
      </div>
    </div>

  </div>'''

    txt = txt[:start] + new_right + txt[end:]
    with open(f, 'w', encoding='utf-8') as fh:
        fh.write(txt)
    print('Right column replaced with Enquiries panel.')
else:
    print('Could not locate section boundaries.')
