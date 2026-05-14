const canvas = document.getElementById('matrix');
const ctx = canvas.getContext('2d');

const glyphs = '01ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%&@<>/\\[]{}+=-*';
let width = 0;
let height = 0;
let columns = [];
let fontSize = 16;
let lastFrame = 0;
const frameDelay = 72;
const fallSpeed = 0.42;

function resize() {
  width = canvas.width = window.innerWidth;
  height = canvas.height = window.innerHeight;
  const count = Math.floor(width / fontSize);
  columns = Array.from({ length: count }, () => Math.floor(Math.random() * -height / fontSize));
}

function draw(timestamp = 0) {
  requestAnimationFrame(draw);

  if (timestamp - lastFrame < frameDelay) {
    return;
  }
  lastFrame = timestamp;

  ctx.fillStyle = 'rgba(0, 8, 2, 0.115)';
  ctx.fillRect(0, 0, width, height);
  ctx.font = `${fontSize}px monospace`;

  for (let i = 0; i < columns.length; i++) {
    const char = glyphs[Math.floor(Math.random() * glyphs.length)];
    const x = i * fontSize;
    const y = columns[i] * fontSize;

    ctx.fillStyle = Math.random() > 0.992 ? '#d7ffd7' : '#00ff66';
    ctx.fillText(char, x, y);

    if (y > height && Math.random() > 0.985) {
      columns[i] = Math.floor(Math.random() * -25);
    }
    columns[i] += fallSpeed;
  }
}

window.addEventListener('resize', resize);
resize();
requestAnimationFrame(draw);
