// Мобильное меню: кнопка-«бургер» открывает панель со всеми разделами
(function () {
  var burger = document.getElementById('burger');
  var nav = document.getElementById('nav');
  if (!burger || !nav) return;
  function setOpen(open) {
    document.body.classList.toggle('nav-open', open);
    burger.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  burger.addEventListener('click', function () { setOpen(!document.body.classList.contains('nav-open')); });
  nav.addEventListener('click', function (e) { if (e.target.tagName === 'A') setOpen(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setOpen(false); });
  window.addEventListener('resize', function () { if (window.innerWidth > 860) setOpen(false); });
})();
