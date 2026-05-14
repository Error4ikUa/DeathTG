const canvas = document.getElementById('matrix');
const ctx = canvas.getContext('2d');

const glyphs = '01ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%&@<>/\\[]{}+=-*';
let width = 0;
let height = 0;
let columns = [];
let fontSize = 16;

function resize() {
  width = canvas.width = window.innerWidth;
  height = canvas.height = window.innerHeight;
  const count = Math.floor(width / fontSize);
  columns = Array.from({ length: count }, () => Math.floor(Math.random() * -height));
}

function draw() {
  ctx.fillStyle = 'rgba(0, 8, 2, 0.08)';
  ctx.fillRect(0, 0, width, height);
  ctx.font = `${fontSize}px monospace`;

  for (let i = 0; i < columns.length; i++) {
    const char = glyphs[Math.floor(Math.random() * glyphs.length)];
    const x = i * fontSize;
    const y = columns[i] * fontSize;

    ctx.fillStyle = Math.random() > 0.985 ? '#d7ffd7' : '#00ff66';
    ctx.fillText(char, x, y);

    if (y > height && Math.random() > 0.975) {
      columns[i] = 0;
    }
    columns[i]++;
  }

  requestAnimationFrame(draw);
}

window.addEventListener('resize', resize);
resize();
draw();
