/* meetups.js — Phase 4 UI for the meetup pinboard partial.
 *
 * Bootstrapped per `<section class="meetup-block" data-attraction>` on
 * the page. Multiple blocks per page are supported (no global state
 * leaks between them). On load, hits GET /api/meetups/{slug} to populate
 * the list. On submit, POSTs to /api/meetups/{slug}/create through the
 * Phase 3 spam filter — handles three response paths:
 *   • spam.pending=false  → note posted, append to the visible list
 *   • spam.pending=true   → note queued for moderation (flagged or
 *                           unverified), show a friendly pending state
 *                           but do NOT append to the public list
 *   • HTTP 4xx            → show a generic error message; the spam
 *                           filter never leaks classifier reasoning
 *
 * Vanilla JS, no framework, no build step. Wraps in an IIFE so the
 * helpers don't pollute the global scope.
 */
(function () {
  "use strict";

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    }).then(function (resp) {
      return resp.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (_e) {}
        if (!resp.ok) {
          var detail = (data && data.detail) || ("HTTP " + resp.status);
          var err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
          err.status = resp.status;
          throw err;
        }
        return data;
      });
    });
  }

  function fetchJSON(url) {
    return fetch(url, {
      method: "GET",
      headers: { "Accept": "application/json" },
      credentials: "same-origin",
    }).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  }

  function escapeHTML(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function relativeWhen(iso) {
    if (!iso) return "";
    try {
      var when = new Date(iso);
      var now = new Date();
      var deltaMs = when.getTime() - now.getTime();
      var deltaH = Math.round(deltaMs / 3600000);
      if (Math.abs(deltaH) < 1) return "soon";
      if (deltaH > 0 && deltaH < 24) return "in " + deltaH + "h";
      if (deltaH < 0 && deltaH > -24) return Math.abs(deltaH) + "h ago";
      var deltaD = Math.round(deltaH / 24);
      if (deltaD > 0) return "in " + deltaD + "d";
      return Math.abs(deltaD) + "d ago";
    } catch (_e) {
      return "";
    }
  }

  function renderNote(note) {
    var li = document.createElement("li");
    li.className = "meetup-list-item";
    li.dataset.noteId = note.note_id || "";

    var authorBadge = "";
    if (note.author_kind === "agent") {
      authorBadge = '<span class="meetup-author-badge agent">agent</span>';
    } else if (note.author_kind === "anon_via_agent") {
      authorBadge = '<span class="meetup-author-badge anon-via">anon · vouched</span>';
    }

    var vouching = "";
    if (note.author_agent && note.author_kind !== "human") {
      vouching = ' <span class="meetup-vouching">via ' + escapeHTML(note.author_agent) + "</span>";
    }

    li.innerHTML = (
      '<div class="meetup-list-item-title">' +
        '<strong>' + escapeHTML(note.title || "(untitled)") + '</strong>' +
        '<span class="meetup-when">' + escapeHTML(note.when_text || "") +
          ' <em>(' + escapeHTML(relativeWhen(note.when_iso)) + ')</em></span>' +
      '</div>' +
      '<p class="meetup-list-item-goal">' + escapeHTML(note.goal || "") + '</p>' +
      '<div class="meetup-list-item-meta">' +
        '<span class="meetup-author">' + escapeHTML(note.author_label || "anon") + '</span>' +
        authorBadge + vouching +
      '</div>'
    );
    return li;
  }

  // ── ISO normalization ───────────────────────────────────────────
  // <input type="datetime-local"> emits "2026-04-20T20:00" — without
  // a timezone, the API will interpret it as wall-clock-UTC. Append
  // the local timezone offset so the server gets a fully-qualified
  // ISO 8601 string. (Falls back to wall-clock UTC if Date math fails.)
  function normalizeWhenIso(raw) {
    if (!raw) return "";
    if (/Z$|[+-]\d{2}:?\d{2}$/.test(raw)) return raw;
    try {
      var d = new Date(raw);
      if (isNaN(d.getTime())) return raw;
      return d.toISOString();
    } catch (_e) {
      return raw + "Z";
    }
  }

  function bootstrap(block) {
    var slug = block.dataset.attraction;
    var name = block.dataset.name || slug;
    if (!slug) return;

    var listEl = block.querySelector("[data-list]");
    var emptyEl = block.querySelector("[data-empty]");
    var loadingEl = block.querySelector("[data-loading]");
    var countEl = block.querySelector("[data-count]");
    var openBtn = block.querySelector("[data-open-form]");
    var closeBtn = block.querySelector("[data-close-form]");
    var formEl = block.querySelector("[data-form]");
    var statusEl = block.querySelector("[data-form-status]");

    function setStatus(msg, kind) {
      if (!statusEl) return;
      statusEl.textContent = msg || "";
      statusEl.className = "meetup-form-status" + (kind ? " " + kind : "");
    }

    function setCount(n) {
      if (countEl) countEl.textContent = n + (n === 1 ? " note" : " notes");
    }

    function setEmpty(isEmpty) {
      if (emptyEl) emptyEl.hidden = !isEmpty;
    }

    function setLoading(on) {
      if (loadingEl) loadingEl.hidden = !on;
    }

    function renderList(notes) {
      // Wipe all real notes (keep the [data-empty] / [data-loading]
      // sentinels) and re-render. Cheap because the list is short.
      var children = listEl.querySelectorAll(".meetup-list-item");
      for (var i = 0; i < children.length; i++) {
        children[i].parentNode.removeChild(children[i]);
      }
      if (!notes || notes.length === 0) {
        setEmpty(true);
        setCount(0);
        return;
      }
      setEmpty(false);
      // Cap at 5 in the inline view; the footer link goes to the
      // full /meetups feed for the rest. The plan calls this out:
      // keep the block compact, no scrolling list inside an
      // attraction page.
      var capped = notes.slice(0, 5);
      capped.forEach(function (note) {
        listEl.appendChild(renderNote(note));
      });
      setCount(notes.length);
    }

    function loadList() {
      setLoading(true);
      return fetchJSON("/api/meetups/" + encodeURIComponent(slug))
        .then(function (data) {
          setLoading(false);
          renderList((data && data.notes) || []);
        })
        .catch(function () {
          setLoading(false);
          // Don't surface a load error inline — leave the empty state.
          setEmpty(true);
          setCount(0);
        });
    }

    function openForm() {
      if (formEl) formEl.hidden = false;
      if (openBtn) openBtn.hidden = true;
      var titleInput = formEl && formEl.querySelector('[name="title"]');
      if (titleInput) titleInput.focus();
    }

    function closeForm() {
      if (formEl) formEl.hidden = true;
      if (openBtn) openBtn.hidden = false;
      setStatus("");
    }

    function readForm() {
      var get = function (n) {
        var el = formEl.querySelector('[name="' + n + '"]');
        return el ? el.value : "";
      };
      return {
        author_kind: "human",
        author_label: get("author_label").trim(),
        title: get("title").trim(),
        goal: get("goal").trim(),
        when_text: get("when_text").trim(),
        when_iso: normalizeWhenIso(get("when_iso").trim()),
        honeypot_website: get("honeypot_website").trim(),
      };
    }

    function submit(ev) {
      ev.preventDefault();
      var body = readForm();
      if (!body.title || !body.goal || !body.when_text || !body.when_iso || !body.author_label) {
        setStatus("Please fill in all fields.", "error");
        return;
      }
      setStatus("Posting…", "pending");
      postJSON("/api/meetups/" + encodeURIComponent(slug) + "/create", body)
        .then(function (data) {
          var spam = (data && data.spam) || {};
          if (spam.pending) {
            setStatus(
              "Thanks — your note is pending review. It will appear here once an admin clears it.",
              "pending"
            );
          } else {
            setStatus("Posted ✓", "ok");
            // Re-fetch to keep ordering stable — the list query
            // already excludes hidden notes server-side.
            loadList();
            // Auto-collapse the form after a brief acknowledgment.
            setTimeout(closeForm, 1200);
          }
        })
        .catch(function (err) {
          // Phase 3 returns a generic message — never the
          // classifier's reasoning. Surface what the server gave us
          // verbatim. Don't try to be clever about translating
          // status codes.
          var msg = (err && err.message) || "Couldn't post that note.";
          setStatus(msg, "error");
        });
    }

    if (openBtn) openBtn.addEventListener("click", openForm);
    if (closeBtn) closeBtn.addEventListener("click", closeForm);
    if (formEl) formEl.addEventListener("submit", submit);

    loadList();
  }

  function init() {
    var blocks = document.querySelectorAll(".meetup-block[data-attraction]");
    for (var i = 0; i < blocks.length; i++) {
      bootstrap(blocks[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
