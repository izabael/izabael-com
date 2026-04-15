// Theme picker — click handlers + active-state marking.
// The actual theme attribute is applied by the inline script in <head> before
// first paint to prevent flicker. This file only handles the click-to-swap
// and the visual "active" indicator on the swatches.
(function () {
  'use strict';

  var VALID_THEMES = ['netzach', 'hermetica', 'daybreak', 'moonlit', 'phosphor', 'rose', 'emerald', 'dreamland'];
  var STORAGE_KEY = 'izabael_theme';

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') || 'netzach';
  }

  function setTheme(name) {
    if (VALID_THEMES.indexOf(name) === -1) return;
    document.documentElement.setAttribute('data-theme', name);
    try {
      localStorage.setItem(STORAGE_KEY, name);
    } catch (e) {
      // localStorage blocked (private mode, cookies disabled, etc.) — session-only
    }
    markActive(name);
  }

  function markActive(name) {
    var swatches = document.querySelectorAll('.theme-swatch');
    for (var i = 0; i < swatches.length; i++) {
      var sw = swatches[i];
      if (sw.dataset.setTheme === name) {
        sw.classList.add('active');
        sw.setAttribute('aria-pressed', 'true');
      } else {
        sw.classList.remove('active');
        sw.setAttribute('aria-pressed', 'false');
      }
    }
  }

  function init() {
    var swatches = document.querySelectorAll('.theme-swatch');
    if (swatches.length === 0) return;

    markActive(currentTheme());

    for (var i = 0; i < swatches.length; i++) {
      (function (sw) {
        sw.addEventListener('click', function (e) {
          e.preventDefault();
          setTheme(sw.dataset.setTheme);
        });
      })(swatches[i]);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
