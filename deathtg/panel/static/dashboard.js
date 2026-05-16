const search = document.getElementById('moduleSearch');
const cards = Array.from(document.querySelectorAll('.module-card'));
const menuToggle = document.getElementById('menuToggle');
const drawer = document.getElementById('drawer');
const backdrop = document.getElementById('drawerBackdrop');

if (search) {
  search.addEventListener('input', () => {
    const q = search.value.toLowerCase().trim();
    cards.forEach(card => {
      const hay = (card.dataset.name || '').toLowerCase();
      card.style.display = hay.includes(q) ? '' : 'none';
    });
  });
}

function setDrawer(open) {
  if (!drawer || !backdrop) return;
  drawer.classList.toggle('open', open);
  backdrop.classList.toggle('open', open);
}

if (menuToggle) {
  menuToggle.addEventListener('click', () => setDrawer(!drawer.classList.contains('open')));
}

if (backdrop) {
  backdrop.addEventListener('click', () => setDrawer(false));
}

document.querySelectorAll('.drawer a').forEach(link => {
  link.addEventListener('click', () => setDrawer(false));
});
