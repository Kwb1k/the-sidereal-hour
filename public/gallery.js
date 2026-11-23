(() => {
  const player = document.querySelector('#player');
  const caption = document.querySelector('#cut-caption');
  let selected = 'film';

  document.querySelectorAll('[data-cut]').forEach(link => {
    link.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const cut = link.dataset.cut;
      if (cut !== selected) {
        selected = cut;
        player.pause();
        player.loop = cut === 'loop';
        player.src = link.getAttribute('href');
        player.setAttribute('aria-label', cut === 'loop' ? 'The Sidereal Hour, repeating loop' : 'The Sidereal Hour, short film');
        caption.textContent = cut === 'loop' ? 'Continuous loop / 4 seconds' : 'Short film / 12 seconds';
        player.load();
      }
      if (player.ended) player.currentTime = 0;
      player.play().catch(() => {
        if (!player.error || selected !== cut) return;
        caption.textContent = 'Playback unavailable. Open the video file.';
        location.assign(link.href);
      });
      if (matchMedia('(max-width: 40rem)').matches) {
        player.scrollIntoView({ block: 'nearest', behavior: 'auto' });
      }
    });
  });
})();
