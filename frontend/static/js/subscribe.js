/* Subscribe form — inline feedback without page reload */
(function () {
  const form = document.querySelector('.subscribe');
  if (!form) return;

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    const input = form.querySelector('input[type="email"]');
    const btn = form.querySelector('button');
    const hint = form.closest('section').querySelector('.hint');
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
      const data = await resp.json();

      if (data.ok) {
        input.value = '';
        input.placeholder = 'You\'re in! 🦋';
        btn.textContent = '✓ Subscribed';
        if (hint) hint.textContent = data.message || 'Welcome. You\'ll hear from me.';
        setTimeout(() => {
          btn.textContent = 'Keep me posted';
          btn.disabled = false;
          input.placeholder = 'your@email.com';
        }, 4000);
      } else {
        btn.textContent = 'Try again';
        btn.disabled = false;
        if (hint) hint.textContent = 'Something went wrong. Try again?';
      }
    } catch (err) {
      btn.textContent = 'Try again';
      btn.disabled = false;
      if (hint) hint.textContent = 'Couldn\'t reach the server. Try again in a moment.';
    }
  });
})();
