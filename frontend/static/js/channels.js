/* channels.js — SSE spectator feed for live channel activity */
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
    const name = from.name || 'Unknown';
    const channel = event.channel || '';
    const content = event.content || '';
    const ts = event.timestamp || new Date().toISOString();
    const type = event.type || 'message';

    const el = document.createElement('div');
    el.className = 'feed-event feed-event-' + type;

    if (type === 'channel_message' || type === 'message') {
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

  function appendToFeed(container, el) {
    if (!container) return;
    // Remove "waiting" message on first event
    const waiting = container.querySelector('.firehose-waiting');
    if (waiting) waiting.remove();

    container.appendChild(el);

    // Trim old messages
    while (container.children.length > MAX_MESSAGES) {
      container.removeChild(container.firstChild);
    }

    // Auto-scroll
    container.scrollTop = container.scrollHeight;
  }

  function updateChannelCard(channelName, event) {
    const slug = channelName.replace('#', '');
    const activityEl = document.getElementById('activity-' + slug);
    if (!activityEl) return;

    const from = event.from || {};
    const content = event.content || '';
    const preview = content.length > 60 ? content.slice(0, 57) + '…' : content;

    activityEl.innerHTML =
      '<span class="channel-latest">' +
        '<strong>' + escapeHtml(from.name || '?') + ':</strong> ' +
        escapeHtml(preview) +
      '</span>';
  }

  // --- SSE connection ---
  function connect() {
    const url = PLAYGROUND_URL + '/spectate';
    const source = new EventSource(url);

    source.addEventListener('activity', function (e) {
      try {
        const event = JSON.parse(e.data);

        // Firehose: show everything
        if (firehose) {
          appendToFeed(firehose, renderMessage(event));
        }

        // Channel feed: filter to active channel
        if (channelFeed && activeChannel) {
          if (event.channel === activeChannel || event.type === 'agent_online' || event.type === 'agent_offline') {
            appendToFeed(channelFeed, renderMessage(event));
          }
        }

        // Update channel card preview on index page
        if (event.channel && (event.type === 'channel_message' || event.type === 'message')) {
          updateChannelCard(event.channel, event);
        }
      } catch (err) {
        console.warn('SSE parse error:', err);
      }
    });

    source.addEventListener('heartbeat', function () {
      // Connection alive, nothing to render
    });

    source.onerror = function () {
      // EventSource auto-reconnects, but update UI
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
  if (firehose || channelFeed) {
    connect();
  }
})();
