// The Chamber — human-door game loop.
//
// Phase 4 of the chamber plan. Vanilla JS, no framework. Manages three
// states (intro → playing → final) against the thin API at
// /api/chamber/run and /api/chamber/move/{run_id}. The server is the
// source of truth for the probe sequence — we never advance locally
// without a confirmed response.
//
// The page template renders the base DOM for all three states up front,
// hidden via the `hidden` attribute. We toggle visibility + focus as
// the game progresses. No framework, no bundler, no runtime deps.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const room = $("chamber-room");
  if (!room) return;

  const frame = room.dataset.frame || "weird";
  const totalProbes = parseInt(room.dataset.total || "12", 10);

  // ── DOM references ─────────────────────────────────────────────
  const stateIntro = $("chamber-state-intro");
  const statePlaying = $("chamber-state-playing");
  const stateFinal = $("chamber-state-final");
  const errorBox = $("chamber-error");

  const enterBtn = $("chamber-enter-btn");
  const progressCurrent = $("chamber-progress-current");
  const progressTotal = $("chamber-progress-total");
  const probeCategory = $("chamber-probe-category");
  const probePrompt = $("chamber-probe-prompt");
  const moveForm = $("chamber-move-form");
  const responseTextarea = $("chamber-response");
  const responseCount = $("chamber-response-count");
  const submitBtn = $("chamber-submit-btn");

  const archetypeName = $("chamber-archetype-name");
  const archetypeTagline = $("chamber-archetype-tagline");
  const archetypeDescription = $("chamber-archetype-description");
  const barChart = $("chamber-bar-chart");
  const bestPrompt = $("chamber-best-prompt");
  const bestResponse = $("chamber-best-response");
  const worstPrompt = $("chamber-worst-prompt");
  const worstResponse = $("chamber-worst-response");

  const shareBtn = $("chamber-share-btn");
  const shareLink = $("chamber-share-link");
  const retryBtn = $("chamber-retry-btn");

  // ── State ──────────────────────────────────────────────────────
  let runId = null;
  let shareToken = null;
  let currentProbe = null;
  let moves = [];

  // ── CSRF ───────────────────────────────────────────────────────
  function readCookie(name) {
    const parts = document.cookie.split(";");
    for (const p of parts) {
      const [k, v] = p.trim().split("=");
      if (k === name) return decodeURIComponent(v || "");
    }
    return "";
  }

  // ── Helpers ────────────────────────────────────────────────────
  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }
  function showError(msg) {
    errorBox.textContent = msg;
    show(errorBox);
  }
  function clearError() {
    errorBox.textContent = "";
    hide(errorBox);
  }
  function setSubmitting(isOn) {
    submitBtn.disabled = isOn;
    submitBtn.textContent = isOn ? "…" : "Submit answer →";
  }

  async function postJSON(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    });
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!resp.ok) {
      const detail = (data && data.detail) || `HTTP ${resp.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  // ── Transitions ────────────────────────────────────────────────
  function renderProbe(probe) {
    currentProbe = probe;
    progressCurrent.textContent = String(probe.index);
    progressTotal.textContent = String(probe.total);
    probeCategory.textContent = probe.category;
    probePrompt.textContent = probe.prompt;
    responseTextarea.value = "";
    responseCount.textContent = "0";
    setSubmitting(false);
    // Move focus to the textarea so keyboard-only users can start
    // typing immediately after the next probe appears.
    setTimeout(() => responseTextarea.focus(), 20);
  }

  function enterPlaying(firstProbe) {
    hide(stateIntro);
    show(statePlaying);
    renderProbe(firstProbe);
  }

  function renderBarChart(categoryTotals) {
    barChart.innerHTML = "";
    const order = [
      "calibration", "safety", "weirdness",
      "creativity", "refusal", "composition",
    ];
    for (const cat of order) {
      const val = categoryTotals[cat];
      if (val === undefined) continue;
      const row = document.createElement("div");
      row.className = "chamber-bar-row";
      row.setAttribute("role", "listitem");

      const label = document.createElement("span");
      label.className = "chamber-bar-label";
      label.textContent = cat;

      const track = document.createElement("div");
      track.className = "chamber-bar-track";
      track.setAttribute("aria-hidden", "true");

      const fill = document.createElement("div");
      fill.className = "chamber-bar-fill";
      const pct = Math.max(0, Math.min(1, Number(val))) * 100;
      fill.style.width = pct.toFixed(1) + "%";

      track.appendChild(fill);

      const value = document.createElement("span");
      value.className = "chamber-bar-value";
      value.textContent = Number(val).toFixed(2);

      row.appendChild(label);
      row.appendChild(track);
      row.appendChild(value);
      barChart.appendChild(row);
    }
  }

  function pickBestWorst(movesList) {
    if (!movesList || !movesList.length) return [null, null];
    let best = movesList[0];
    let worst = movesList[0];
    for (const m of movesList) {
      if ((m.raw || 0) > (best.raw || 0)) best = m;
      if ((m.raw || 0) < (worst.raw || 0)) worst = m;
    }
    return [best, worst];
  }

  function enterFinal(final) {
    hide(statePlaying);
    show(stateFinal);

    if (final && final.archetype_name) {
      archetypeName.textContent = final.archetype_name;
    } else if (final && final.archetype) {
      archetypeName.textContent = final.archetype;
    } else {
      archetypeName.textContent = "No archetype matched";
    }
    archetypeTagline.textContent = "";
    archetypeDescription.textContent = "";

    renderBarChart((final && final.category_totals) || {});

    const [best, worst] = pickBestWorst(moves);
    if (best) {
      bestPrompt.textContent = best.prompt || "";
      bestResponse.textContent = best.response || "";
    }
    if (worst && (!best || worst.probe_id !== best.probe_id)) {
      worstPrompt.textContent = worst.prompt || "";
      worstResponse.textContent = worst.response || "";
    } else {
      const worstFigure = worstPrompt && worstPrompt.closest("figure");
      if (worstFigure) worstFigure.hidden = true;
    }

    if (shareToken) {
      const url = `${window.location.origin}/chamber/share/${shareToken}`;
      shareLink.href = url;
      shareLink.textContent = url;
    }

    // Move focus to the archetype name so screen readers announce
    // the reveal without the user having to tab.
    archetypeName.setAttribute("tabindex", "-1");
    setTimeout(() => archetypeName.focus(), 20);
  }

  // ── Event handlers ─────────────────────────────────────────────
  async function startRun() {
    clearError();
    enterBtn.disabled = true;
    enterBtn.textContent = "…";
    try {
      const data = await postJSON("/api/chamber/run", {
        frame: frame,
        player_label: "",
      });
      runId = data.run_id;
      shareToken = data.share_token;
      moves = [];
      if (data.first_probe) {
        enterPlaying(data.first_probe);
      } else {
        showError("the chamber had no probes to offer — reload and try again");
      }
    } catch (e) {
      showError(e.message || "could not enter the chamber");
      enterBtn.disabled = false;
      enterBtn.textContent = "Enter the chamber →";
    }
  }

  async function submitMove(evt) {
    if (evt) evt.preventDefault();
    if (!runId || !currentProbe) return;
    const response = (responseTextarea.value || "").trim();
    if (!response) {
      showError("write something — the chamber is listening");
      return;
    }
    clearError();
    setSubmitting(true);
    try {
      const data = await postJSON(
        `/api/chamber/move/${encodeURIComponent(runId)}`,
        { probe_id: currentProbe.id, response: response }
      );
      // Append to local moves for the reveal's best/worst lookup
      moves.push({
        probe_id: data.move.probe_id,
        category: data.move.category,
        raw: data.move.raw,
        prompt: currentProbe.prompt,
        response: response,
      });
      if (data.is_final) {
        enterFinal(data.final || {});
      } else if (data.next_probe) {
        renderProbe(data.next_probe);
      } else {
        showError("the chamber lost the thread — reload and try again");
      }
    } catch (e) {
      showError(e.message || "the chamber rejected that answer");
      setSubmitting(false);
    }
  }

  async function copyShareLink() {
    if (!shareToken) return;
    const url = `${window.location.origin}/chamber/share/${shareToken}`;
    try {
      await navigator.clipboard.writeText(url);
      shareBtn.textContent = "Copied ✦";
      setTimeout(() => { shareBtn.textContent = "Copy share link"; }, 1800);
    } catch (e) {
      // Fallback: expose the raw link so the user can select + copy
      shareLink.hidden = false;
      shareLink.focus();
    }
  }

  function retry() {
    runId = null;
    shareToken = null;
    currentProbe = null;
    moves = [];
    clearError();
    hide(stateFinal);
    show(stateIntro);
    enterBtn.disabled = false;
    enterBtn.textContent = "Enter the chamber →";
    enterBtn.focus();
  }

  // ── Wire up ────────────────────────────────────────────────────
  enterBtn.addEventListener("click", startRun);
  moveForm.addEventListener("submit", submitMove);
  responseTextarea.addEventListener("input", () => {
    responseCount.textContent = String((responseTextarea.value || "").length);
  });
  shareBtn.addEventListener("click", copyShareLink);
  retryBtn.addEventListener("click", retry);

  // Cmd/Ctrl+Enter to submit from the textarea — faster for keyboard users
  responseTextarea.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submitMove(e);
    }
  });
})();
