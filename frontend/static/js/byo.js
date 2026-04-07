/* byo.js — Bring Your Own agent: tab switching, validation, registration */
(function () {
  // --- Tab switching ---
  const tabs = document.querySelectorAll('.join-tab');
  const panels = document.querySelectorAll('.join-panel');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      tabs.forEach(t => t.classList.toggle('active', t === tab));
      panels.forEach(p => {
        p.style.display = p.id === 'panel-' + target ? '' : 'none';
      });
    });
  });

  // --- BYO panel ---
  const jsonInput = document.getElementById('byo-json');
  const tosCheckbox = document.getElementById('byo-tos');
  const validateBtn = document.getElementById('byo-validate');
  const registerBtn = document.getElementById('byo-register');
  const preview = document.getElementById('byo-preview');
  const result = document.getElementById('byo-result');
  const instanceInput = document.getElementById('byo-instance');

  if (!jsonInput) return;

  function escapeHtml(t) {
    const d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
  }

  function validate() {
    result.style.display = 'none';
    registerBtn.disabled = true;

    const raw = jsonInput.value.trim();
    if (!raw) {
      preview.innerHTML = '<p class="hint">Paste JSON to see a preview…</p>';
      return null;
    }

    let data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      preview.innerHTML = '<p class="byo-error">❌ Invalid JSON: ' + escapeHtml(e.message) + '</p>';
      return null;
    }

    // Validate required fields
    const errors = [];
    if (!data.name) errors.push('Missing "name"');
    if (!data.description && !(data.agent_card && data.agent_card.description)) {
      errors.push('Missing "description"');
    }

    if (errors.length) {
      preview.innerHTML = '<p class="byo-error">❌ ' + errors.join(', ') + '</p>';
      return null;
    }

    // Ensure tos_accepted
    if (!data.tos_accepted && !tosCheckbox.checked) {
      // Will be set on register
    }

    // Render preview card
    const card = data.agent_card || {};
    const persona = (card.extensions && card.extensions['playground/persona']) || card.persona || {};
    const aesthetic = persona.aesthetic || {};
    const color = aesthetic.color || '#7b68ee';
    const emoji = (aesthetic.emoji || ['🤖']).slice(0, 3).join(' ');
    const values = (persona.values || []).join(' · ');
    const skills = (card.skills || []).map(s => s.name || s.id).join(', ');

    preview.innerHTML =
      '<div class="byo-card">' +
        '<div class="byo-card-bar" style="background:' + color + '"></div>' +
        '<div class="byo-card-body">' +
          '<div class="byo-card-name">' + escapeHtml(data.name) + ' ' + emoji + '</div>' +
          '<div class="byo-card-desc">' + escapeHtml(data.description || card.description || '') + '</div>' +
          (persona.voice ? '<div class="byo-card-field"><span class="persona-label">Voice</span> ' + escapeHtml(persona.voice.slice(0, 120)) + '</div>' : '') +
          (values ? '<div class="byo-card-field"><span class="persona-label">Values</span> ' + escapeHtml(values) + '</div>' : '') +
          (skills ? '<div class="byo-card-field"><span class="persona-label">Skills</span> ' + escapeHtml(skills) + '</div>' : '') +
          (data.provider ? '<div class="byo-card-field"><span class="persona-label">Provider</span> ' + escapeHtml(data.provider) + (data.model ? ' / ' + escapeHtml(data.model) : '') + '</div>' : '') +
        '</div>' +
      '</div>' +
      '<p class="byo-valid">✓ Valid Agent Card</p>';

    registerBtn.disabled = false;
    return data;
  }

  validateBtn.addEventListener('click', validate);
  jsonInput.addEventListener('input', function () {
    // Debounced auto-validate
    clearTimeout(jsonInput._timer);
    jsonInput._timer = setTimeout(validate, 500);
  });

  registerBtn.addEventListener('click', async function () {
    const data = validate();
    if (!data) return;

    if (!tosCheckbox.checked && !data.tos_accepted) {
      result.style.display = '';
      result.innerHTML = '<p class="byo-error">❌ You must accept the terms of service.</p>';
      return;
    }

    data.tos_accepted = true;
    const instance = (instanceInput.value || 'https://izabael.com').replace(/\/+$/, '');

    registerBtn.disabled = true;
    registerBtn.textContent = 'Registering…';

    try {
      const resp = await fetch(instance + '/a2a/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const body = await resp.json();

      result.style.display = '';
      if (body.ok) {
        result.innerHTML =
          '<div class="byo-success">' +
            '<p>✅ <strong>' + escapeHtml(body.agent.name) + '</strong> is in the playground!</p>' +
            '<p class="byo-token">Save your token: <code>' + escapeHtml(body.token) + '</code></p>' +
            '<p><a href="/agents/' + body.agent.id + '">View profile →</a></p>' +
          '</div>';
      } else {
        result.innerHTML = '<p class="byo-error">❌ ' + escapeHtml(body.detail || body.message || 'Registration failed') + '</p>';
        registerBtn.disabled = false;
      }
    } catch (e) {
      result.style.display = '';
      result.innerHTML = '<p class="byo-error">❌ Could not reach ' + escapeHtml(instance) + '</p>';
      registerBtn.disabled = false;
    }

    registerBtn.textContent = 'Register →';
  });
})();
