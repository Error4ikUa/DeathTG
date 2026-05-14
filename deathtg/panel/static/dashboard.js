const search = document.getElementById('moduleSearch');
const cards = Array.from(document.querySelectorAll('.module-card'));

if (search) {
  search.addEventListener('input', () => {
    const q = search.value.toLowerCase().trim();
    cards.forEach(card => {
      const hay = (card.dataset.name || '').toLowerCase();
      card.style.display = hay.includes(q) ? '' : 'none';
    });
  });
}
