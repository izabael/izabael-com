/* Subscribe form — inline feedback without page reload. Handles any
   number of .subscribe forms on the page (landing hero, post footer…). */
(function () {
  const forms = document.querySelectorAll('.subscribe');
  if (!forms.length) return;

  function wire(form) {
    const input = form.querySelector('input[type="email"]');
    const btn = form.querySelector('button');
    const section = form.closest('section') || form.parentElement;
    const hint = section ? section.querySelector('.hint') : null;
    const originalBtn = btn ? btn.textContent : 'Keep me posted';

    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      const email = input.value.trim();
      if (!email) return;

      btn.disabled = true;
      btn.textContent = 'Sending…';

      try {
        const resp = await fetch('/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: 'email=' + encodeURIComponent(email),
        });
        const data = await resp.json().catch(() => ({}));

        if (resp.ok && data.ok) {
          input.value = '';
          input.placeholder = "You're in! 🦋";
          btn.textContent = '✓ Check your email';
          if (hint) hint.textContent = data.message || 'A confirmation link is on the way.';
          setTimeout(() => {
            btn.textContent = originalBtn;
            btn.disabled = false;
            input.placeholder = 'your@email.com';
          }, 5000);
        } else {
          btn.textContent = 'Try again';
          btn.disabled = false;
          if (hint) hint.textContent = (data && data.detail) || 'Something went wrong. Try again?';
        }
      } catch (err) {
        btn.textContent = 'Try again';
        btn.disabled = false;
        if (hint) hint.textContent = "Couldn't reach the server. Try again in a moment.";
      }
    });
  }

  forms.forEach(wire);
})();
