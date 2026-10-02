// subpool site: copy buttons, the phone menu, the diagram's pause button and ?theme=.
// The page works without this file.
(function () {
  'use strict';

  // ?theme=light or ?theme=dark forces an appearance (for previews and screenshots).
  var theme = new URLSearchParams(location.search).get('theme');
  if (theme === 'light' || theme === 'dark') {
    document.documentElement.setAttribute('data-theme', theme);
    document.querySelectorAll('source[data-theme-dark]').forEach(function (source) {
      source.media = theme === 'dark' ? 'all' : 'not all';
    });
  }

  // The phone menu closes once you pick a section, press Escape or tap outside it.
  var sheet = document.querySelector('.nav-sheet');
  if (sheet) {
    sheet.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () { sheet.open = false; });
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && sheet.open) {
        sheet.open = false;
        sheet.querySelector('summary').focus();
      }
    });
    document.addEventListener('click', function (event) {
      if (sheet.open && !sheet.contains(event.target)) sheet.open = false;
    });
  }

  // The diagram's animation can be paused, and it rests while the diagram is off screen.
  var diagram = document.querySelector('.diagram');
  var pause = diagram && diagram.querySelector('.diagram-pause');
  if (pause) {
    var motion = window.matchMedia('(prefers-reduced-motion: no-preference)');
    var pauseLabel = pause.querySelector('.diagram-pause-label');
    var showPause = function () { pause.hidden = !motion.matches; };
    showPause();
    if (motion.addEventListener) motion.addEventListener('change', showPause);
    pause.addEventListener('click', function () {
      var paused = diagram.classList.toggle('is-paused');
      pauseLabel.textContent = paused ? 'Play animation' : 'Pause animation';
    });
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (entries) {
        diagram.classList.toggle('is-offscreen', !entries[entries.length - 1].isIntersecting);
      }).observe(diagram);
    }
  }

  var status = document.getElementById('copy-status');

  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      area.remove();
      if (ok) { resolve(); } else { reject(new Error('copy failed')); }
    });
  }

  // data-copy holds the text itself; data-copy-from names the element whose text is copied (the setup prompt).
  document.querySelectorAll('button[data-copy], button[data-copy-from]').forEach(function (button) {
    var label = button.querySelector('.copy-label');
    var source = button.hasAttribute('data-copy-from') && document.getElementById(button.getAttribute('data-copy-from'));
    var timer;
    if (source === null) return;
    button.hidden = false;
    button.addEventListener('click', function () {
      copy(source ? source.textContent : button.getAttribute('data-copy')).then(function () {
        button.classList.add('is-copied');
        if (label) label.textContent = 'Copied';
        if (status) status.textContent = button.getAttribute('data-copied') || 'Install command copied to the clipboard.';
      }, function () {
        if (label) label.textContent = 'Press ⌘C';
        var code = source || button.parentElement.querySelector('code');
        if (code) window.getSelection().selectAllChildren(code);
      }).then(function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          button.classList.remove('is-copied');
          if (label) label.textContent = 'Copy';
          if (status) status.textContent = '';
        }, 2000);
      });
    });
  });
})();
