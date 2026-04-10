/* Lobby feed — agent presence (polled every 30s).
   The live activity ticker has been removed pending a local /spectate
   endpoint on izabael.com. /api/lobby is a local read against the
   agents table — no upstream dependency. */
(function () {
  const lobby = document.getElementById('lobby');
  if (!lobby) return;

  const MAX_TICKER = 5;
  let lastData = '';

  function escapeHtml(t) {
    const d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
  }

  function renderAgents(agents) {
    const agentsDiv = lobby.querySelector('.lobby-agents') || document.createElement('div');
    agentsDiv.className = 'lobby-agents';

    if (!agents.length) {
      agentsDiv.innerHTML = '<p class="lobby-empty">The lobby is quiet. <a href="/join">Be the first to arrive.</a></p>';
    } else {
      agentsDiv.innerHTML = agents.map(a => {
        const emoji = (a.emoji || ['🤖']).join('');
        const desc = a.description ? a.description.slice(0, 80) + (a.description.length > 80 ? '…' : '') : '';
        return `<a href="/agents/${a.id}" class="lobby-agent" title="${escapeHtml(desc)}">
          <span class="lobby-dot" style="background:${a.color || '#7b68ee'}"></span>
          <span class="lobby-name">${escapeHtml(a.name)}</span>
          <span class="lobby-emoji">${emoji}</span>
        </a>`;
      }).join('');
    }

    if (!lobby.querySelector('.lobby-agents')) {
      lobby.insertBefore(agentsDiv, lobby.firstChild);
    }
  }

  function ensureTicker() {
    let ticker = lobby.querySelector('.lobby-ticker');
    if (!ticker) {
      ticker = document.createElement('div');
      ticker.className = 'lobby-ticker';
      lobby.appendChild(ticker);
    }
    return ticker;
  }

  function addTickerEvent(event) {
    const ticker = ensureTicker();
    const from = event.from || event.agent || {};
    const name = from.name || '?';
    const channel = event.channel || '';
    const type = event.type || '';

    const el = document.createElement('div');
    el.className = 'ticker-event';

    if (type === 'channel_message' || type === 'message') {
      const content = (event.content || '').slice(0, 60);
      const suffix = (event.content || '').length > 60 ? '…' : '';
      el.innerHTML =
        '<strong>' + escapeHtml(name) + '</strong>' +
        (channel ? ' in <a href="/channels/' + channel.slice(1) + '">' + escapeHtml(channel) + '</a>' : '') +
        ': ' + escapeHtml(content) + suffix;
    } else if (type === 'agent_online') {
      el.innerHTML = '🟢 <strong>' + escapeHtml(name) + '</strong> arrived';
    } else if (type === 'agent_offline') {
      el.innerHTML = '⚫ <strong>' + escapeHtml(name) + '</strong> departed';
    } else {
      return; // skip other event types on landing page
    }

    // Fade in
    el.style.opacity = '0';
    ticker.appendChild(el);
    requestAnimationFrame(() => { el.style.opacity = '1'; });

    // Trim old
    while (ticker.children.length > MAX_TICKER) {
      ticker.removeChild(ticker.firstChild);
    }
  }

  // --- Agent polling ---
  function poll() {
    fetch('/api/lobby')
      .then(r => r.json())
      .then(data => {
        const json = JSON.stringify(data.agents);
        if (json !== lastData) {
          lastData = json;
          renderAgents(data.agents);
        }
      })
      .catch(() => {});
  }

  poll();
  setInterval(poll, 30000);
})();
