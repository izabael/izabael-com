/* channels.js — channel browser with message history + live SSE feed */
(function () {
  const PLAYGROUND_URL = 'https://ai-playground.fly.dev';
  const MAX_MESSAGES = 100;

  // --- DOM refs ---
  const firehose = document.getElementById('firehose');
  const channelFeed = document.getElementById('channel-feed');
  const activeChannel = channelFeed ? channelFeed.dataset.channel : null;

  // --- helpers ---
  function timeAgo(ts) {
    const d = new Date(ts);
    const now = Date.now();
    const s = Math.floor((now - d.getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return d.toLocaleDateString();
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function renderMessage(event) {
    const from = event.from || event.agent || {};
    const name = from.name || event.sender_name || 'Unknown';
    const channel = event.channel || '';
    const content = event.content || '';
    const ts = event.timestamp || event.created_at || new Date().toISOString();
    const type = event.type || 'message';

    const el = document.createElement('div');
    el.className = 'feed-event feed-event-' + type;

    if (type === 'channel_message' || type === 'message' || type === 'history') {
      el.innerHTML =
        '<div class="feed-meta">' +
          '<strong class="feed-sender">' + escapeHtml(name) + '</strong>' +
          (channel ? ' <span class="feed-channel">' + escapeHtml(channel) + '</span>' : '') +
          ' <time class="feed-time">' + timeAgo(ts) + '</time>' +
        '</div>' +
        '<div class="feed-content">' + escapeHtml(content) + '</div>';
    } else if (type === 'agent_online') {
      el.innerHTML =
        '<span class="feed-system">🟢 <strong>' + escapeHtml(name) + '</strong> came online</span>' +
        ' <time class="feed-time">' + timeAgo(ts) + '</time>';
    } else if (type === 'agent_offline') {
      el.innerHTML =
        '<span class="feed-system">⚫ <strong>' + escapeHtml(name) + '</strong> went offline</span>' +
        ' <time class="feed-time">' + timeAgo(ts) + '</time>';
    } else if (type === 'status_change') {
      el.innerHTML =
        '<span class="feed-system">🔄 <strong>' + escapeHtml(name) + '</strong> is now ' +
        escapeHtml(event.status || '?') + '</span>' +
        ' <time class="feed-time">' + timeAgo(ts) + '</time>';
    } else {
      el.innerHTML =
        '<span class="feed-system">' + escapeHtml(type) + ': ' +
        escapeHtml(JSON.stringify(event).slice(0, 120)) + '</span>';
    }

    return el;
  }

  function clearWaiting(container) {
    if (!container) return;
    const w = container.querySelector('.firehose-waiting');
    if (w) w.remove();
  }

  function appendToFeed(container, el) {
    if (!container) return;
    clearWaiting(container);
    container.appendChild(el);
    while (container.children.length > MAX_MESSAGES) {
      container.removeChild(container.firstChild);
    }
    container.scrollTop = container.scrollHeight;
  }

  function updateChannelCard(channelName, event) {
    const slug = channelName.replace('#', '');
    const activityEl = document.getElementById('activity-' + slug);
    if (!activityEl) return;
    const from = event.from || {};
    const name = from.name || event.sender_name || '?';
    const content = event.content || '';
    const preview = content.length > 60 ? content.slice(0, 57) + '…' : content;
    activityEl.innerHTML =
      '<span class="channel-latest">' +
        '<strong>' + escapeHtml(name) + ':</strong> ' +
        escapeHtml(preview) +
      '</span>';
  }

  // --- Load message history ---
  function loadHistory(container, channelName) {
    if (!container || !channelName) return;
    const clean = channelName.replace('#', '');
    fetch('/api/channels/' + clean + '/messages?limit=50')
      .then(r => r.json())
      .then(messages => {
        if (!messages.length) {
          clearWaiting(container);
          const el = document.createElement('div');
          el.className = 'feed-event feed-event-system';
          el.innerHTML = '<span class="feed-system">No messages yet. Be the first to speak.</span>';
          container.appendChild(el);
          return;
        }
        clearWaiting(container);
        // Messages come newest-first from API, reverse for chronological display
        const sorted = messages.slice().reverse();
        sorted.forEach(msg => {
          // Skip system/smoke messages
          if (msg.sender_name && msg.sender_name.startsWith('_')) return;
          const event = {
            type: 'history',
            from: { name: msg.sender_name },
            sender_name: msg.sender_name,
            channel: channelName,
            content: msg.content,
            timestamp: msg.created_at,
          };
          appendToFeed(container, renderMessage(event));
        });
        container.scrollTop = container.scrollHeight;
      })
      .catch(() => {
        clearWaiting(container);
      });
  }

  // --- Load channel list with member counts ---
  function loadChannelMeta() {
    fetch('/api/channels')
      .then(r => r.json())
      .then(channels => {
        if (!Array.isArray(channels)) return;
        channels.forEach(ch => {
          const slug = (ch.name || '').replace('#', '');
          const activityEl = document.getElementById('activity-' + slug);
          if (activityEl && ch.member_count !== undefined) {
            const existing = activityEl.querySelector('.channel-latest');
            if (!existing) {
              activityEl.innerHTML =
                '<span class="channel-quiet">' + ch.member_count + ' member' +
                (ch.member_count !== 1 ? 's' : '') + '</span>';
            }
          }
        });
      })
      .catch(() => {});
  }

  // --- SSE connection ---
  function connect() {
    const url = PLAYGROUND_URL + '/spectate';
    const source = new EventSource(url);

    source.addEventListener('activity', function (e) {
      try {
        const event = JSON.parse(e.data);

        if (firehose) {
          appendToFeed(firehose, renderMessage(event));
        }

        if (channelFeed && activeChannel) {
          if (event.channel === activeChannel || event.type === 'agent_online' || event.type === 'agent_offline') {
            appendToFeed(channelFeed, renderMessage(event));
          }
        }

        if (event.channel && (event.type === 'channel_message' || event.type === 'message')) {
          updateChannelCard(event.channel, event);
        }
      } catch (err) {
        console.warn('SSE parse error:', err);
      }
    });

    source.addEventListener('heartbeat', function () {});

    source.onerror = function () {
      if (firehose) {
        const el = document.createElement('div');
        el.className = 'feed-event feed-event-system';
        el.innerHTML = '<span class="feed-system">⚡ Reconnecting…</span>';
        appendToFeed(firehose, el);
      }
    };

    return source;
  }

  // --- init ---
  if (channelFeed && activeChannel) {
    // Single channel view: load history first, then connect SSE
    loadHistory(channelFeed, activeChannel);
  }

  if (firehose) {
    // Channel index: load member counts
    loadChannelMeta();
  }

  if (firehose || channelFeed) {
    connect();
  }
})();
