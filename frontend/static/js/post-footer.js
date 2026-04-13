/* Post footer: copy-link button handler. */
(function () {
  document.querySelectorAll('.share-btn--copy').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var url = btn.dataset.copyUrl || window.location.href;
      var label = btn.querySelector('.share-btn-label');
      var original = label ? label.textContent : 'Copy link';
      var flash = function (text) {
        if (label) label.textContent = text;
        setTimeout(function () {
          if (label) label.textContent = original;
        }, 2000);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(
          function () { flash('Copied! ✨'); },
          function () { flash('Press Ctrl-C'); }
        );
      } else {
        flash('Press Ctrl-C');
      }
    });
  });
})();
