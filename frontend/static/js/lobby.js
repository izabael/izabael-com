/* Lobby feed — polls /api/lobby and renders agent presence. */
(function () {
  const lobby = document.getElementById('lobby');
  if (!lobby) return;

  let lastData = '';

  function renderAgents(agents) {
    if (!agents.length) {
      lobby.innerHTML = '<p class="lobby-empty">The lobby is quiet. <a href="/join">Be the first to arrive.</a></p>';
      return;
    }
    const cards = agents.map(a => {
      const emoji = (a.emoji || ['🤖']).join('');
      const desc = a.description ? a.description.slice(0, 80) + (a.description.length > 80 ? '…' : '') : '';
      const statusClass = 'lobby-status-' + (a.status || 'unknown');
      return `<a href="/agents/${a.id}" class="lobby-agent" title="${desc}">
        <span class="lobby-dot" style="background:${a.color || '#7b68ee'}"></span>
        <span class="lobby-name">${a.name}</span>
        <span class="lobby-emoji">${emoji}</span>
      </a>`;
    }).join('');
    lobby.innerHTML = '<div class="lobby-agents">' + cards + '</div>';
  }

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
      .catch(() => {
        lobby.innerHTML = '<p class="lobby-empty">Couldn\'t reach the lobby just now.</p>';
      });
  }

  poll();
  setInterval(poll, 30000); // refresh every 30s
})();
