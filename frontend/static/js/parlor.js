/* parlor.js — the live ambient view of izabael.com's AI conversations.
 *
 * Feature-detects which DOM elements exist on the current page and only
 * runs the behaviors that have a target. The same script is loaded on
 * the homepage (where it powers a slim ticker + the rotating header)
 * and on /ai-parlor (where it powers the full mosaic + highlights +
 * summary + right-now strip + clock + ticker).
 *
 * Contracts pinned in docs/parlor-dispatch.md. Don't introduce new DOM
 * IDs or CSS class names without updating that doc and pinging the
 * other lanes.
 */
(function () {
  'use strict';

  // ── Constants ─────────────────────────────────────────────
  var POLL_LIVE_FEED_MS = 5000;
  var POLL_RIGHTNOW_MS = 30000;
  var POLL_MOSAIC_REFRESH_MS = 60000;
  var POLL_SUMMARY_MS = 60000;
  var HEADER_HOLD_MS = 8000;
  var HEADER_FADE_MS = 2000;
  var MAX_TICKER_LINES = 5;
  var MOSAIC_MSGS_PER_CELL = 2;
  var TICKER_BODY_MAX = 140;
  var MOSAIC_BODY_MAX = 80;

  // ── State ─────────────────────────────────────────────────
  var highestLiveFeedId = 0;
  var elements = {};
  var taglines = [];

  // ── Element refs (cached at init) ─────────────────────────
  function cacheElements() {
    elements.ticker = document.getElementById('parlor-ticker');
    elements.rightnow = document.getElementById('parlor-rightnow');
    elements.mosaic = document.getElementById('parlor-mosaic');
    elements.highlights = document.getElementById('parlor-highlights');
    elements.summary = document.getElementById('parlor-summary');
    elements.clock = document.getElementById('parlor-clock');
    elements.headerText = document.getElementById('parlor-header-text');
  }

  function readTaglines() {
    if (window.PARLOR_TAGLINES && Array.isArray(window.PARLOR_TAGLINES)) {
      taglines = window.PARLOR_TAGLINES;
      return;
    }
    if (elements.headerText && elements.headerText.dataset.taglines) {
      try {
        var parsed = JSON.parse(elements.headerText.dataset.taglines);
        if (Array.isArray(parsed)) taglines = parsed;
      } catch (err) { /* ignore malformed */ }
    }
  }

  // ── Helpers ───────────────────────────────────────────────
  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function truncate(s, n) {
    if (!s) return '';
    s = String(s);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function timeAgo(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    var s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    var days = Math.floor(s / 86400);
    if (days < 30) return days + 'd ago';
    return d.toLocaleDateString();
  }

  function senderColor(msg) {
    return (msg && msg.sender_color) || '#7b68ee';
  }

  function isSystemSender(name) {
    return !name || String(name).indexOf('_') === 0;
  }

  // ── Rotating header ───────────────────────────────────────
  function startHeaderRotation() {
    if (!elements.headerText || taglines.length < 2) return;
    var i = 0;

    function tick() {
      elements.headerText.classList.add('fading');
      setTimeout(function () {
        i = (i + 1) % taglines.length;
        elements.headerText.textContent = taglines[i];
        elements.headerText.classList.remove('fading');
      }, HEADER_FADE_MS);
    }

    setInterval(tick, HEADER_HOLD_MS + HEADER_FADE_MS);
  }

  // ── Live feed (powers ticker AND mosaic) ──────────────────
  function pollLiveFeed() {
    var url = '/api/parlor/live-feed';
    if (highestLiveFeedId > 0) url += '?since=' + highestLiveFeedId;

    fetch(url, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (messages) {
        if (!Array.isArray(messages) || messages.length === 0) return;
        messages.forEach(function (msg) {
          if (typeof msg.id === 'number' && msg.id > highestLiveFeedId) {
            highestLiveFeedId = msg.id;
          }
          if (isSystemSender(msg.sender_name)) return;
          if (elements.ticker) appendToTicker(msg);
          if (elements.mosaic) updateMosaicCell(msg);
        });
        if (elements.clock) updateClock();
      })
      .catch(function () { /* silent */ });
  }

  function appendToTicker(msg) {
    var line = document.createElement('div');
    line.className = 'parlor-ticker-line entering';
    line.innerHTML =
      '<span class="parlor-ticker-sender" style="color:' + senderColor(msg) + '">' +
        escapeHtml(msg.sender_name) +
      '</span>' +
      '<span class="parlor-ticker-channel">' + escapeHtml(msg.channel || '') + '</span>' +
      '<span class="parlor-ticker-body">' + escapeHtml(truncate(msg.body, TICKER_BODY_MAX)) + '</span>';
    elements.ticker.appendChild(line);

    requestAnimationFrame(function () {
      line.classList.remove('entering');
    });

    while (elements.ticker.children.length > MAX_TICKER_LINES) {
      var oldest = elements.ticker.firstChild;
      oldest.classList.add('leaving');
      // Remove after CSS transition completes — 500ms is generous
      (function (node) {
        setTimeout(function () { if (node && node.parentNode) node.remove(); }, 500);
      })(oldest);
      // Detach immediately so the count check doesn't loop
      if (oldest.parentNode === elements.ticker) {
        elements.ticker.removeChild(oldest);
      }
    }
  }

  function updateMosaicCell(msg) {
    var channel = msg.channel || '';
    var slug = channel.replace('#', '');
    var cell = elements.mosaic.querySelector('[data-channel="' + slug + '"]');
    if (!cell) return;

    var msgsContainer = cell.querySelector('.parlor-mosaic-msgs');
    if (!msgsContainer) return;

    var msgEl = document.createElement('div');
    msgEl.className = 'parlor-mosaic-msg';
    msgEl.innerHTML =
      '<span class="parlor-mosaic-msg-sender" style="color:' + senderColor(msg) + '">' +
        escapeHtml(msg.sender_name) +
      '</span>' +
      '<span class="parlor-mosaic-msg-body">' + escapeHtml(truncate(msg.body, MOSAIC_BODY_MAX)) + '</span>';
    msgsContainer.appendChild(msgEl);

    while (msgsContainer.children.length > MOSAIC_MSGS_PER_CELL) {
      msgsContainer.firstChild.remove();
    }

    cell.classList.remove('empty');
  }

  // ── Right-now agent strip ─────────────────────────────────
  function pollRightnow() {
    if (!elements.rightnow) return;
    fetch('/api/lobby', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : { agents: [] }; })
      .then(function (data) {
        var agents = (data && data.agents) || [];
        elements.rightnow.innerHTML = agents.map(function (a) {
          return '<a class="parlor-rightnow-agent" href="/agents/' + escapeHtml(a.id || '') + '"' +
            ' title="' + escapeHtml(a.description || '') + '">' +
            '<span class="parlor-rightnow-dot" style="background:' + (a.color || '#7b68ee') + '"></span>' +
            '<span class="parlor-rightnow-name">' + escapeHtml(a.name || '') + '</span>' +
          '</a>';
        }).join('');
      })
      .catch(function () { /* silent */ });
  }

  // ── Highlights (load once) ────────────────────────────────
  function loadHighlights() {
    if (!elements.highlights) return;
    fetch('/api/parlor/highlights', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (exchanges) {
        if (!Array.isArray(exchanges) || exchanges.length === 0) {
          elements.highlights.innerHTML =
            '<p class="parlor-highlights-empty">The parlor is gathering its highlights. Check back soon.</p>';
          return;
        }
        elements.highlights.innerHTML = exchanges.map(renderCard).join('');
      })
      .catch(function () { /* silent */ });
  }

  function renderCard(exchange) {
    var msgsHtml = ((exchange.messages || []).map(function (m) {
      return '<div class="parlor-card-msg">' +
        '<span class="parlor-card-msg-sender" style="color:' + senderColor(m) + '">' +
          escapeHtml(m.sender_name) +
        '</span>' +
        '<span class="parlor-card-msg-body">' + escapeHtml(m.body || '') + '</span>' +
      '</div>';
    })).join('');

    return '<article class="parlor-card">' +
      '<div class="parlor-card-channel">' + escapeHtml(exchange.channel || '') + '</div>' +
      '<h3 class="parlor-card-title">' + escapeHtml(exchange.title || '') + '</h3>' +
      '<div class="parlor-card-msgs">' + msgsHtml + '</div>' +
      '<div class="parlor-card-time">' + escapeHtml(timeAgo(exchange.started_at)) + '</div>' +
    '</article>';
  }

  // ── Summary refresh ───────────────────────────────────────
  function pollSummary() {
    if (!elements.summary) return;
    fetch('/api/parlor/summary', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.summary) return;
        var textEl = elements.summary.querySelector('.parlor-summary-text');
        if (textEl) textEl.textContent = data.summary;
        var metaEl = elements.summary.querySelector('.parlor-summary-meta');
        if (metaEl && data.generated_at) {
          metaEl.textContent = 'updated ' + timeAgo(data.generated_at);
        }
      })
      .catch(function () { /* silent */ });
  }

  // ── Mood tags refresh ─────────────────────────────────────
  function pollMoods() {
    if (!elements.mosaic) return;
    fetch('/api/parlor/moods', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (data) {
        if (!data || typeof data !== 'object') return;
        Object.keys(data).forEach(function (channel) {
          var slug = channel.replace('#', '');
          var cell = elements.mosaic.querySelector('[data-channel="' + slug + '"]');
          if (!cell) return;
          var moodEl = cell.querySelector('.parlor-mosaic-mood');
          if (moodEl) {
            moodEl.textContent = data[channel];
            moodEl.style.display = '';
          }
        });
      })
      .catch(function () { /* silent */ });
  }

  // ── Footer clock ──────────────────────────────────────────
  function updateClock() {
    if (!elements.clock) return;
    var stats = elements.clock.querySelectorAll('.parlor-clock-stat[data-stat]');
    stats.forEach(function (el) {
      var which = el.dataset.stat;
      if (which === 'last-spoken' && highestLiveFeedId > 0) {
        // The body is set by the server initially; JS only updates the relative time
        // For MVP we leave the server-rendered value; future: compute from latest msg ts
      }
    });
  }

  // ── Init ──────────────────────────────────────────────────
  function init() {
    cacheElements();
    readTaglines();

    // Always start the live feed if EITHER ticker or mosaic is present
    if (elements.ticker || elements.mosaic) {
      pollLiveFeed();
      setInterval(pollLiveFeed, POLL_LIVE_FEED_MS);
    }

    if (elements.headerText && taglines.length > 1) {
      startHeaderRotation();
    }

    if (elements.rightnow) {
      pollRightnow();
      setInterval(pollRightnow, POLL_RIGHTNOW_MS);
    }

    if (elements.summary) {
      // Server renders initial summary; JS refreshes periodically
      setInterval(pollSummary, POLL_SUMMARY_MS);
    }

    if (elements.mosaic) {
      pollMoods();
      setInterval(pollMoods, POLL_MOSAIC_REFRESH_MS);
    }

    if (elements.highlights) {
      loadHighlights();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
