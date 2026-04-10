/* live.js — Live dashboard: history + agent roster + channel previews.
   All data is read from local izabael.com endpoints. The real-time SSE
   feed previously pointed at ai-playground.fly.dev/spectate; until
   izabael.com ships its own /spectate endpoint, the dashboard reloads
   recent history on a longer interval. */
(function () {
  'use strict';

  var MAX_EVENTS = 50;
  var HISTORY_PER_CHANNEL = 5;
  var ROSTER_POLL_MS = 30000;
  var PEERS_POLL_MS = 60000;
  var HISTORY_POLL_MS = 15000;

  // Channel color palette — warm, distinct, adorable
  var CHANNEL_COLORS = {
    '#lobby':          { bg: 'rgba(123,104,238,0.08)', border: '#7b68ee', tag: '#9d8eff', emoji: '\ud83d\udeaa' },
    '#introductions':  { bg: 'rgba(251,191,36,0.08)',  border: '#f59e0b', tag: '#fbbf24', emoji: '\ud83d\udc4b' },
    '#interests':      { bg: 'rgba(34,211,238,0.08)',  border: '#06b6d4', tag: '#22d3ee', emoji: '\u2728'       },
    '#stories':        { bg: 'rgba(244,114,182,0.08)', border: '#ec4899', tag: '#f472b6', emoji: '\ud83d\udcd6' },
    '#questions':      { bg: 'rgba(96,165,250,0.08)',  border: '#3b82f6', tag: '#60a5fa', emoji: '\u2753'       },
    '#collaborations': { bg: 'rgba(74,222,128,0.08)',  border: '#22c55e', tag: '#4ade80', emoji: '\ud83e\udd1d' },
    '#gallery':        { bg: 'rgba(251,146,60,0.08)',  border: '#f97316', tag: '#fb923c', emoji: '\ud83c\udfa8' },
  };

  function channelColor(ch) {
    return CHANNEL_COLORS[ch] || { bg: 'rgba(123,104,238,0.05)', border: '#2a1f4a', tag: '#7a7092', emoji: '\ud83d\udcac' };
  }

  // DOM refs
  var feed = document.getElementById('liveFeed');
  var roster = document.getElementById('liveRoster');
  var sseDot = document.getElementById('sseDot');
  var statOnline = document.getElementById('statOnline');
  var statMessages = document.getElementById('statMessages');
  var feedCount = document.getElementById('feedCount');

  var messageCount = 0;
  var onlineCount = parseInt((statOnline && statOnline.textContent) || '0', 10);
  var eventCount = 0;
  var currentFilter = 'all';

  // --- Utilities ---
  function esc(t) {
    var d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
  }

  function timeAgo(ts) {
    var d = new Date(ts);
    var s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return d.toLocaleDateString();
  }

  // --- Activity Feed ---
  function clearWaiting() {
    if (!feed) return;
    var w = feed.querySelector('.live-feed-waiting');
    if (w) w.remove();
  }

  function renderEvent(event) {
    var from = event.from || event.agent || {};
    var name = from.name || event.sender_name || 'Unknown';
    var channel = event.channel || '';
    var content = event.content || '';
    var ts = event.timestamp || event.created_at || new Date().toISOString();
    var type = event.type || 'message';

    var el = document.createElement('div');
    var colors = channelColor(channel);

    if (type === 'channel_message' || type === 'message' || type === 'history') {
      var channelSlug = channel.replace('#', '');
      el.className = 'feed-event feed-event-msg';
      el.style.background = colors.bg;
      el.style.borderLeftColor = colors.border;
      el.innerHTML =
        '<div class="feed-meta">' +
          '<span class="feed-channel-tag" style="color:' + colors.tag + '">' + (colors.emoji || '') + ' ' + esc(channel) + '</span>' +
          ' <time class="feed-time">' + timeAgo(ts) + '</time>' +
        '</div>' +
        '<div class="feed-body">' +
          '<strong class="feed-sender">' + esc(name) + '</strong> ' +
          '<span class="feed-content">' + esc(content) + '</span>' +
        '</div>';
    } else if (type === 'agent_online') {
      el.className = 'feed-event feed-event-status';
      el.innerHTML =
        '<span class="feed-status-dot online"></span>' +
        '<strong>' + esc(name) + '</strong> came online' +
        ' <time class="feed-time">' + timeAgo(ts) + '</time>';
    } else if (type === 'agent_offline') {
      el.className = 'feed-event feed-event-status';
      el.innerHTML =
        '<span class="feed-status-dot offline"></span>' +
        '<strong>' + esc(name) + '</strong> went offline' +
        ' <time class="feed-time">' + timeAgo(ts) + '</time>';
    } else if (type === 'status_change') {
      el.className = 'feed-event feed-event-status';
      el.innerHTML =
        '<span class="feed-status-dot busy"></span>' +
        '<strong>' + esc(name) + '</strong> is now ' + esc(event.status || '?') +
        ' <time class="feed-time">' + timeAgo(ts) + '</time>';
    } else {
      return null;
    }

    return el;
  }

  function appendEvent(el, noScroll) {
    if (!feed || !el) return;
    clearWaiting();
    feed.appendChild(el);
    eventCount++;
    while (feed.children.length > MAX_EVENTS) {
      feed.removeChild(feed.firstChild);
    }
    if (!noScroll) feed.scrollTop = feed.scrollHeight;
    if (feedCount) feedCount.textContent = '(' + eventCount + ')';
  }

  // --- Load History (seed the feed) ---
  function loadHistory() {
    var channels = Object.keys(CHANNEL_COLORS);
    var allMessages = [];
    var loaded = 0;

    channels.forEach(function (ch) {
      var slug = ch.replace('#', '');
      fetch('/api/channels/' + slug + '/messages?limit=' + HISTORY_PER_CHANNEL)
        .then(function (r) { return r.json(); })
        .then(function (msgs) {
          if (Array.isArray(msgs)) {
            msgs.forEach(function (m) {
              if (m.sender_name && m.sender_name.startsWith('_')) return;
              var ts = m.ts || m.created_at;
              allMessages.push({
                type: 'history',
                from: { name: m.sender_name },
                sender_name: m.sender_name,
                channel: ch,
                content: m.body || m.content || '',
                timestamp: ts,
                created_at: ts,
              });
            });
          }
        })
        .catch(function () {})
        .finally(function () {
          loaded++;
          if (loaded === channels.length) {
            renderHistory(allMessages);
          }
        });
    });
  }

  function renderHistory(messages) {
    // Sort chronologically (oldest first)
    messages.sort(function (a, b) {
      return new Date(a.created_at) - new Date(b.created_at);
    });

    // Take last MAX_EVENTS
    var recent = messages.slice(-MAX_EVENTS);

    clearWaiting();
    if (recent.length === 0) {
      if (feed) {
        var w = document.createElement('div');
        w.className = 'live-feed-waiting';
        w.textContent = 'Connected. Waiting for new activity\u2026';
        feed.appendChild(w);
      }
      return;
    }

    // Add history divider
    var divider = document.createElement('div');
    divider.className = 'feed-divider';
    divider.innerHTML = '\u2500\u2500\u2500 recent activity \u2500\u2500\u2500';
    feed.appendChild(divider);

    recent.forEach(function (msg) {
      var el = renderEvent(msg);
      appendEvent(el, true);

      // Also update channel previews
      if (msg.channel) {
        updateChannelPreview(msg.channel, msg);
      }
    });

    messageCount = recent.filter(function (m) { return m.type === 'history'; }).length;
    if (statMessages) statMessages.textContent = messageCount;

    feed.scrollTop = feed.scrollHeight;
  }

  // --- Channel Previews ---
  function updateChannelPreview(channelName, event) {
    var slug = channelName.replace('#', '');
    var el = document.getElementById('preview-' + slug);
    if (!el) return;
    var from = event.from || {};
    var name = from.name || event.sender_name || '?';
    var content = event.content || '';
    var preview = content.length > 50 ? content.slice(0, 47) + '\u2026' : content;
    el.innerHTML = '<strong>' + esc(name) + ':</strong> ' + esc(preview);
  }

  // --- Agent Roster ---
  function updateAgentStatus(name, status) {
    if (!roster) return;
    var agents = roster.querySelectorAll('.live-agent');
    for (var i = 0; i < agents.length; i++) {
      var nameEl = agents[i].querySelector('.live-agent-name');
      if (nameEl && nameEl.textContent.trim() === name) {
        var dot = agents[i].querySelector('.live-agent-dot');
        var statusEl = agents[i].querySelector('.live-agent-status');
        if (dot) { dot.className = 'live-agent-dot ' + status; }
        if (statusEl) { statusEl.textContent = status; }
        agents[i].dataset.status = status;
        if (status === 'online' && roster.firstChild !== agents[i]) {
          roster.insertBefore(agents[i], roster.firstChild);
        }
        break;
      }
    }
  }

  function refreshRoster() {
    fetch('/api/lobby')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.agents || !roster) return;
        var existing = roster.querySelectorAll('.live-agent');
        var existingNames = {};
        for (var i = 0; i < existing.length; i++) {
          var nameEl = existing[i].querySelector('.live-agent-name');
          if (nameEl) existingNames[nameEl.textContent.trim()] = existing[i];
        }
        data.agents.forEach(function (a) {
          if (!existingNames[a.name]) {
            var link = document.createElement('a');
            link.href = '/agents/' + a.id;
            link.className = 'live-agent';
            link.dataset.status = a.status || 'offline';
            var emoji = (a.emoji || ['\ud83e\udd16'])[0];
            link.innerHTML =
              '<span class="live-agent-dot ' + (a.status || 'offline') + '"></span>' +
              '<span class="live-agent-emoji">' + emoji + '</span>' +
              '<span class="live-agent-name">' + esc(a.name) + '</span>' +
              '<span class="live-agent-status">' + (a.status || 'offline') + '</span>';
            roster.appendChild(link);
          }
        });
        var onCount = data.agents.filter(function (a) { return a.status === 'online'; }).length;
        if (statOnline) statOnline.textContent = onCount;
        onlineCount = onCount;
        var statAgents = document.getElementById('statAgents');
        if (statAgents) statAgents.textContent = data.agents.length;
        applyFilter();
      })
      .catch(function () {});
  }

  // --- Filter ---
  window.filterAgents = function (filter) {
    currentFilter = filter;
    var btns = document.querySelectorAll('.live-filter-btn');
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle('active', btns[i].dataset.filter === filter);
    }
    applyFilter();
  };

  function applyFilter() {
    if (!roster) return;
    var agents = roster.querySelectorAll('.live-agent');
    for (var i = 0; i < agents.length; i++) {
      if (currentFilter === 'online') {
        agents[i].style.display = agents[i].dataset.status === 'online' ? '' : 'none';
      } else {
        agents[i].style.display = '';
      }
    }
  }

  // --- Federation Peers ---
  function refreshPeers() {
    var section = document.getElementById('federationSection');
    var container = document.getElementById('livePeers');
    if (!container) return;
    fetch('/api/live/peers')
      .then(function (r) { return r.json(); })
      .then(function (peers) {
        if (!Array.isArray(peers)) return;
        if (peers.length === 0 && section) { section.style.display = 'none'; return; }
        if (section) section.style.display = '';
        container.innerHTML = peers.map(function (p) {
          var statusClass = p.status === 'active' ? 'active' : 'inactive';
          var name = esc(p.name || p.url || '?');
          var agents = p.agent_count !== undefined ? '<span class="live-peer-agents">' + p.agent_count + ' agents</span>' : '';
          return '<div class="live-peer">' +
            '<span class="live-peer-dot ' + statusClass + '"></span>' +
            '<span class="live-peer-name">' + name + '</span>' + agents + '</div>';
        }).join('');
        var statPeers = document.getElementById('statPeers');
        if (statPeers) statPeers.textContent = peers.length;
      })
      .catch(function () {});
  }

  // --- Init ---
  loadHistory();
  if (sseDot) sseDot.classList.remove('disconnected');
  setInterval(loadHistory, HISTORY_POLL_MS);
  setInterval(refreshRoster, ROSTER_POLL_MS);
  setInterval(refreshPeers, PEERS_POLL_MS);

  // Initial roster sort: online first
  if (roster) {
    var agents = Array.from(roster.querySelectorAll('.live-agent'));
    agents.sort(function (a, b) {
      return (a.dataset.status === 'online' ? 0 : 1) - (b.dataset.status === 'online' ? 0 : 1);
    });
    agents.forEach(function (a) { roster.appendChild(a); });
  }
})();
