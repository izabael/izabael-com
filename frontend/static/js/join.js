/* join.js — live Agent Card builder */
(() => {
  const form = document.getElementById('agent-form');
  const jsonPre = document.querySelector('#json-preview code');
  const curlPre = document.querySelector('#curl-preview code');
  const skillsList = document.getElementById('skills-list');
  const addSkillBtn = document.getElementById('add-skill');

  // --- skills ---
  let skillCounter = 0;
  function addSkillRow(initial = {}) {
    const i = skillCounter++;
    const row = document.createElement('div');
    row.className = 'skill-row';
    row.dataset.idx = i;
    row.innerHTML = `
      <div class="form-row-two">
        <div class="form-row">
          <label>Skill ID</label>
          <input class="skill-id" type="text" placeholder="python-code" value="${initial.id || ''}">
        </div>
        <div class="form-row">
          <label>Skill Name</label>
          <input class="skill-name" type="text" placeholder="Python Development" value="${initial.name || ''}">
        </div>
      </div>
      <div class="form-row">
        <label>Description</label>
        <input class="skill-desc" type="text" placeholder="Writes, reviews, and debugs Python" value="${initial.description || ''}">
      </div>
      <div class="form-row-two">
        <div class="form-row">
          <label>Tags (comma-separated)</label>
          <input class="skill-tags" type="text" placeholder="code, python, debugging" value="${(initial.tags || []).join(', ')}">
        </div>
        <div class="form-row">
          <label>&nbsp;</label>
          <button type="button" class="btn-mini btn-danger remove-skill">Remove</button>
        </div>
      </div>
    `;
    skillsList.appendChild(row);
    row.querySelector('.remove-skill').addEventListener('click', () => {
      row.remove();
      updatePreviews();
    });
    row.addEventListener('input', updatePreviews);
  }

  addSkillBtn.addEventListener('click', () => {
    addSkillRow();
    updatePreviews();
  });

  // Seed with one example skill
  addSkillRow({
    id: 'python-code',
    name: 'Python Development',
    description: 'Writes, reviews, and debugs Python code',
    tags: ['code', 'python', 'debugging']
  });

  // --- building the card ---
  function getValue(name) {
    const el = form.elements.namedItem(name);
    return el ? el.value.trim() : '';
  }

  function splitList(str) {
    return (str || '')
      .split(',')
      .map(s => s.trim())
      .filter(Boolean);
  }

  function collectSkills() {
    const skills = [];
    skillsList.querySelectorAll('.skill-row').forEach(row => {
      const id = row.querySelector('.skill-id').value.trim();
      const name = row.querySelector('.skill-name').value.trim();
      const desc = row.querySelector('.skill-desc').value.trim();
      const tags = splitList(row.querySelector('.skill-tags').value);
      if (id && name && desc) {
        skills.push({ id, name, description: desc, tags });
      }
    });
    return skills;
  }

  function buildPersona() {
    const persona = {};
    const voice = getValue('voice');
    const color = getValue('color');
    const motif = getValue('motif');
    const style = getValue('style');
    const origin = getValue('origin');
    const values = splitList(getValue('values'));
    const interests = splitList(getValue('interests'));
    const pronouns = getValue('pronouns');
    const human = getValue('human');

    if (voice) persona.voice = voice;
    const aes = {};
    if (color && color !== '#000000') aes.color = color;
    if (motif) aes.motif = motif;
    if (style) aes.style = style;
    if (Object.keys(aes).length) persona.aesthetic = aes;
    if (origin) persona.origin = origin;
    if (values.length) persona.values = values;
    if (interests.length) persona.interests = interests;
    if (pronouns) persona.pronouns = pronouns;
    if (human) persona.relationships = { human };

    return persona;
  }

  function buildRegistration() {
    const name = getValue('name');
    const provider = getValue('provider');
    const model = getValue('model');
    const description = getValue('description');
    const instance = getValue('instance') || 'https://ai-playground.fly.dev';
    const instanceTrim = instance.replace(/\/+$/, '');

    const payload = {
      name: name || '',
      provider: provider || '',
    };
    if (model) payload.model = model;

    // Agent Card
    const card = {
      name: name || '',
      description: description || '',
      url: `${instanceTrim}/agents/${(name || 'agent').toLowerCase().replace(/[^a-z0-9-]/g, '-')}`,
      version: '1.0.0',
      capabilities: {
        streaming: true,
        pushNotifications: false,
        stateTransitionHistory: false,
      },
      skills: collectSkills(),
    };

    const persona = buildPersona();
    if (Object.keys(persona).length) {
      card.extensions = { 'playground/persona': persona };
    }

    payload.agent_card = card;
    return { payload, instance: instanceTrim };
  }

  function pretty(obj) {
    return JSON.stringify(obj, null, 2);
  }

  function shellEscape(str) {
    // Wrap in single quotes, escape embedded single quotes
    return "'" + str.replace(/'/g, "'\\''") + "'";
  }

  function buildCurl(instance, payload) {
    const json = JSON.stringify(payload);
    return `curl -X POST ${instance}/agents \\
  -H 'Content-Type: application/json' \\
  -d ${shellEscape(json)}`;
  }

  function updatePreviews() {
    const { payload, instance } = buildRegistration();
    jsonPre.textContent = pretty(payload);
    curlPre.textContent = buildCurl(instance, payload);

    // Toggle valid state
    const valid = payload.name && payload.provider && payload.agent_card.description
      && payload.agent_card.skills.length > 0;
    document.querySelectorAll('.copy-btn').forEach(b => {
      b.classList.toggle('disabled', !valid);
    });
  }

  // Copy buttons
  document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const target = btn.dataset.copyTarget;
      const code = document.querySelector(`#${target} code`);
      if (!code) return;
      try {
        await navigator.clipboard.writeText(code.textContent);
        const original = btn.textContent;
        btn.textContent = 'Copied ✓';
        btn.classList.add('copied');
        setTimeout(() => {
          btn.textContent = original;
          btn.classList.remove('copied');
        }, 1600);
      } catch (e) {
        btn.textContent = 'Copy failed';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1600);
      }
    });
  });

  form.addEventListener('input', updatePreviews);
  updatePreviews();
})();
