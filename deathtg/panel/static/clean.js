const toggle = document.getElementById("menuToggle");
const drawer = document.getElementById("drawer");
const overlay = document.getElementById("drawerBackdrop");

function openMenu(value) {
  if (!drawer || !overlay) return;
  drawer.classList.toggle("open", value);
  overlay.classList.toggle("open", value);
}

if (toggle) toggle.onclick = () => openMenu(!drawer.classList.contains("open"));
if (overlay) overlay.onclick = () => openMenu(false);
document.querySelectorAll(".mini-menu a").forEach((a) => (a.onclick = () => openMenu(false)));

(function injectStyles() {
  try {
    const head = document.head || document.getElementsByTagName("head")[0];
    function css(id, href) {
      if (head && !document.getElementById(id)) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = href;
        link.id = id;
        head.appendChild(link);
      }
    }
    css("dtg-theme-css", "/static/theme_cards.css");
    css("dtg-modern-css", "/static/dtg_modern.css");
    css("dtg-avatar-crop-css", "/static/avatar_crop_fix.css");
  } catch (error) {
    console.error("Failed to inject stylesheet", error);
  }
})();

(function warmPageLinks() {
  const seen = new Set();
  const links = [...document.querySelectorAll('a[href^="/"]')];
  for (const link of links) {
    const href = link.getAttribute("href");
    if (!href || href.startsWith("/logout") || seen.has(href)) continue;
    seen.add(href);
    const preload = document.createElement("link");
    preload.rel = "prefetch";
    preload.href = href;
    document.head.appendChild(preload);
  }
})();

function activateTab(id) {
  if (!id) return;
  const btn = document.querySelector(`[data-tab="${id}"]`);
  const pane = document.getElementById(id);
  if (!btn || !pane) return;
  document.querySelectorAll("[data-tab]").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".module-pane").forEach((p) => p.classList.remove("active"));
  btn.classList.add("active");
  pane.classList.add("active");
}

function setupTabs() {
  document.querySelectorAll("[data-tab]").forEach((btn) =>
    btn.addEventListener("click", () => {
      activateTab(btn.dataset.tab);
      history.replaceState(null, "", "#" + btn.dataset.tab);
    }),
  );
  const hash = location.hash.replace("#", "");
  if (hash) activateTab(hash);
}

setupTabs();
window.addEventListener("hashchange", () => activateTab(location.hash.replace("#", "")));

const search = document.getElementById("moduleSearch");
if (search) {
  const cards = [...document.querySelectorAll("#browserPane .module-card,#installedPane .module-card,#installedPane .module-row")];
  search.oninput = () => {
    const q = search.value.trim().toLowerCase();
    if (!q) {
      cards.forEach((c) => (c.style.display = ""));
      return;
    }
    cards.forEach((c) => {
      const hit = (c.dataset.name || c.innerText || "").toLowerCase().includes(q);
      c.style.display = hit ? "" : "none";
    });
  };
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.add("open");
  modal.dispatchEvent(new CustomEvent("dtg:open"));
}

function closeModal(modal) {
  if (modal) modal.classList.remove("open");
}

document.querySelectorAll("[data-modal-open]").forEach((btn) =>
  btn.addEventListener("click", () => openModal(btn.dataset.modalOpen)),
);
document.querySelectorAll("[data-modal-close]").forEach((btn) =>
  btn.addEventListener("click", () => closeModal(btn.closest(".modal"))),
);
document.querySelectorAll(".modal").forEach((modal) =>
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal(modal);
  }),
);

function setupAvatarCrop() {
  const form = document.getElementById("avatarForm");
  const input = document.getElementById("avatarInput");
  const img = document.getElementById("cropImage");
  const frame = document.querySelector(".crop-frame");
  const box = document.getElementById("cropBox");
  const reset = document.getElementById("avatarReset");
  const save = document.getElementById("avatarSave");
  const modal = document.getElementById("cropModal");
  if (!form || !input || !img || !frame || !box) return;

  let file = null;
  let objectUrl = null;
  let naturalW = 0;
  let naturalH = 0;
  let imgScale = 1;
  let frameW = 0;
  let frameH = 0;
  let boxX = 0;
  let boxY = 0;
  let boxSize = 220;
  let mode = null;
  let handle = "";
  let startX = 0;
  let startY = 0;
  let startBoxX = 0;
  let startBoxY = 0;
  let startBoxSize = 0;

  function frameRect() {
    return frame.getBoundingClientRect();
  }

  function imageRect() {
    return {
      left: 0,
      top: 0,
      width: frameW,
      height: frameH,
    };
  }

  function setIdleState() {
    form.classList.remove("has-file");
    frame.classList.remove("has-image");
    img.removeAttribute("src");
    naturalW = 0;
    naturalH = 0;
    frameW = 0;
    frameH = 0;
    frame.style.removeProperty("--crop-frame-w");
    frame.style.removeProperty("--crop-frame-h");
    if (save) save.disabled = true;
  }

  function setFrameForPhoto() {
    const ratio = naturalW && naturalH ? naturalW / naturalH : 1;
    const viewportW = Math.max(320, window.innerWidth || 720);
    const viewportH = Math.max(420, window.innerHeight || 720);
    const maxW = Math.max(280, Math.min(720, viewportW - 56));
    const maxH = Math.max(260, Math.min(560, viewportH * 0.64));
    let width = maxW;
    let height = width / ratio;

    if (height > maxH) {
      height = maxH;
      width = height * ratio;
    }
    if (width < 280) {
      width = 280;
      height = width / ratio;
    }
    if (height < 220) {
      height = 220;
      width = height * ratio;
      if (width > maxW) {
        width = maxW;
        height = width / ratio;
      }
    }

    frameW = Math.round(width);
    frameH = Math.round(height);
    frame.style.setProperty("--crop-frame-w", frameW + "px");
    frame.style.setProperty("--crop-frame-h", frameH + "px");
  }

  function apply() {
    img.style.width = frameW + "px";
    img.style.height = frameH + "px";
    img.style.transform = "none";
    img.style.left = "0";
    img.style.top = "0";
    box.style.setProperty("--crop-box-size", boxSize + "px");
    box.style.setProperty("--crop-box-x", boxX + "px");
    box.style.setProperty("--crop-box-y", boxY + "px");
    box.style.width = boxSize + "px";
    box.style.height = boxSize + "px";
    box.style.left = boxX + "px";
    box.style.top = boxY + "px";
    box.style.transform = "none";
  }

  function clampBox() {
    const minSize = Math.min(92, frameW, frameH);
    const maxSize = Math.max(minSize, Math.min(frameW, frameH));
    boxSize = Math.max(minSize, Math.min(maxSize, boxSize));
    boxX = Math.max(0, Math.min(frameW - boxSize, boxX));
    boxY = Math.max(0, Math.min(frameH - boxSize, boxY));
  }

  function fit() {
    if (!naturalW || !naturalH) return;
    setFrameForPhoto();
    imgScale = frameW / naturalW;
    boxSize = Math.floor(Math.min(frameW, frameH) * 0.74);
    boxX = Math.round((frameW - boxSize) / 2);
    boxY = Math.round((frameH - boxSize) / 2);
    clampBox();
    apply();
  }

  function resizeBox(delta) {
    const centerX = boxX + boxSize / 2;
    const centerY = boxY + boxSize / 2;
    boxSize += delta;
    clampBox();
    boxX = centerX - boxSize / 2;
    boxY = centerY - boxSize / 2;
    clampBox();
    apply();
  }

  input.addEventListener("change", () => {
    file = input.files && input.files[0];
    if (!file) {
      setIdleState();
      return;
    }
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    img.onload = () => {
      naturalW = img.naturalWidth;
      naturalH = img.naturalHeight;
      form.classList.add("has-file");
      frame.classList.add("has-image");
      if (save) save.disabled = false;
      fit();
    };
    img.src = objectUrl;
  });

  frame.addEventListener("pointerdown", (event) => {
    if (!file) return;
    const target = event.target;
    const rect = frameRect();
    const localX = event.clientX - rect.left;
    const localY = event.clientY - rect.top;
    handle = target.classList && target.classList.contains("handle") ? [...target.classList].find((name) => name !== "handle") || "" : "";
    if (handle) {
      mode = "resize";
    } else if (box.contains(target)) {
      mode = "move";
    } else if (target === img || target === frame) {
      mode = "move";
      boxX = localX - boxSize / 2;
      boxY = localY - boxSize / 2;
      clampBox();
      apply();
    } else {
      return;
    }
    startX = event.clientX;
    startY = event.clientY;
    startBoxX = boxX;
    startBoxY = boxY;
    startBoxSize = boxSize;
    frame.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  frame.addEventListener("pointermove", (event) => {
    if (!mode) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (mode === "move") {
      boxX = startBoxX + dx;
      boxY = startBoxY + dy;
    }
    if (mode === "resize") {
      const horizontal = handle.includes("w") ? -dx : handle.includes("e") ? dx : 0;
      const vertical = handle.includes("n") ? -dy : handle.includes("s") ? dy : 0;
      const delta = horizontal && vertical ? (horizontal + vertical) / 2 : horizontal || vertical;
      const nextSize = startBoxSize + delta;
      boxSize = nextSize;
      boxX = startBoxX;
      boxY = startBoxY;
      if (handle.includes("w")) boxX = startBoxX + (startBoxSize - boxSize);
      if (handle.includes("n")) boxY = startBoxY + (startBoxSize - boxSize);
      if (handle === "n" || handle === "s") boxX = startBoxX + (startBoxSize - boxSize) / 2;
      if (handle === "e" || handle === "w") boxY = startBoxY + (startBoxSize - boxSize) / 2;
    }
    clampBox();
    apply();
  });

  frame.addEventListener("pointerup", () => (mode = null));
  frame.addEventListener("pointercancel", () => (mode = null));

  frame.addEventListener("wheel", (event) => {
    if (!file) return;
    event.preventDefault();
    resizeBox(event.deltaY < 0 ? 14 : -14);
  });

  if (reset) reset.onclick = () => fit();

  if (modal) {
    modal.addEventListener("dtg:open", () => {
      if (!input.files || !input.files.length) {
        file = null;
        setIdleState();
      }
    });
  }

  window.addEventListener("resize", () => {
    if (file) fit();
  });

  form.addEventListener("submit", (event) => {
    if (!file || !img.src) return;
    event.preventDefault();
    clampBox();
    apply();

    const size = 512;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const ir = imageRect();
    const srcX = Math.max(0, (boxX - ir.left) / imgScale);
    const srcY = Math.max(0, (boxY - ir.top) / imgScale);
    const srcSize = boxSize / imgScale;

    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, size, size);
    ctx.drawImage(img, srcX, srcY, srcSize, srcSize, 0, 0, size, size);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        const cropped = new File([blob], "avatar.png", { type: "image/png" });
        const dt = new DataTransfer();
        dt.items.add(cropped);
        input.files = dt.files;
        form.submit();
      },
      "image/png",
      0.95,
    );
  });

  setIdleState();
}

setupAvatarCrop();

function shortDay(day) {
  if (!day) return "";
  const parts = String(day).split("-");
  return parts.length === 3 ? `${parts[2]}.${parts[1]}` : day;
}

function showTip(x, y, html) {
  let tip = document.querySelector(".dtg-tooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "dtg-tooltip";
    document.body.appendChild(tip);
  }
  tip.innerHTML = html;
  tip.style.left = x + "px";
  tip.style.top = y + "px";
  tip.style.opacity = "1";
}

function hideTip() {
  const tip = document.querySelector(".dtg-tooltip");
  if (tip) tip.style.opacity = "0";
}

function drawLineChart() {
  const el = document.getElementById("lineChart");
  if (!el) return;
  let points = [];
  const holder = document.getElementById("chartData");
  if (holder) {
    points = [...holder.querySelectorAll("i")].map((i) => ({
      day: i.dataset.day,
      count: Number(i.dataset.count || 0),
      modules: (i.dataset.modules || "").split(", ").filter(Boolean),
    }));
  }

  const today = new Date();
  const data = Array.from({ length: 30 }, (_, idx) => {
    const d = new Date(today);
    d.setDate(today.getDate() - (29 - idx));
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return points.find((x) => x.day === iso) || { day: iso, count: 0, modules: [] };
  });

  const rect = el.getBoundingClientRect();
  const w = Math.max(rect.width || 900, 200);
  const h = Math.max(rect.height || 300, 200);
  const pad = 44;

  if (data.every((x) => x.count === 0)) {
    el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><text x="${w / 2}" y="${h / 2}" text-anchor="middle" font-size="18" fill="rgba(255,255,255,.65)">No activity yet</text></svg>`;
    return;
  }

  const rawMax = Math.max(1, ...data.map((x) => x.count));
  const max = rawMax <= 2 ? 2 : Math.ceil(rawMax * 1.15);
  const nonZeroDays = data.filter((x) => x.count > 0).length;
  const sparse = nonZeroDays <= 2;
  const pts = data.map((d, i) => {
    const x = pad + (i * (w - pad * 2)) / (data.length - 1);
    const y = h - pad - (d.count / max) * (h - pad * 2);
    return { ...d, x, y };
  });

  const line = pts.map((pt, i) => (i ? "L" : "M") + pt.x + " " + pt.y).join(" ");
  const area = line + ` L ${pts.at(-1)?.x || pad} ${h - pad} L ${pts[0]?.x || pad} ${h - pad} Z`;
  const vLines = pts
    .map((pt, idx) => (idx % 5 === 0 || idx === pts.length - 1 ? `<line x1="${pt.x}" y1="${pad}" x2="${pt.x}" y2="${h - pad}"/>` : ""))
    .join("");
  const hRows = sparse ? 4 : 6;
  const hLines = Array.from({ length: hRows }, (_, i) => {
    const y = pad + (i * (h - pad * 2)) / (hRows - 1);
    return `<line x1="${pad}" y1="${y}" x2="${w - pad}" y2="${y}"/>`;
  }).join("");
  const yLabels = Array.from({ length: hRows }, (_, i) => {
    const value = Math.round((max / (hRows - 1)) * i);
    const y = h - pad - (value / max) * (h - pad * 2);
    return `<text x="${pad - 8}" y="${y + 4}" font-size="10" text-anchor="end">${value}</text>`;
  }).join("");
  const xLabels = pts
    .map((pt, idx) =>
      idx % 5 === 0 || idx === pts.length - 1
        ? `<text x="${pt.x}" y="${h - pad + 16}" font-size="10" text-anchor="middle">${shortDay(pt.day)}</text>`
        : "",
    )
    .join("");
  const dots = pts
    .map((pt, i) => {
      const active = pt.count > 0 || i === pts.length - 1;
      const r = pt.count > 0 ? 4 : 2.4;
      const opacity = active ? 1 : 0.35;
      return `<circle class="dot" data-i="${i}" cx="${pt.x}" cy="${pt.y}" r="${r}" style="opacity:${opacity}"></circle>`;
    })
    .join("");

  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><g class="grid">${vLines}${hLines}</g>${yLabels}${xLabels}<path class="area" d="${area}"/><path class="line" d="${line}"/>${dots}</svg>`;
  el.querySelectorAll(".dot").forEach((dot) => {
    const pt = pts[Number(dot.dataset.i)];
    dot.addEventListener("mousemove", (event) => showTip(event.clientX, event.clientY, `<b>${shortDay(pt.day)}</b> - ${pt.modules.join(", ") || "No modules"}`));
    dot.addEventListener("mouseleave", hideTip);
  });
}

drawLineChart();

(function setupAccentPreview() {
  const body = document.body;
  const accentInputs = [...document.querySelectorAll('input[name="accent"]')];
  if (!body || !accentInputs.length) return;

  const glyphs = ['✦', '✧', '✶', '✹', '✺', '✷', '✴', '✦', '+', '*'];

  function emitBurst(source) {
    const rect = (source && source.getBoundingClientRect) ? source.getBoundingClientRect() : {left: window.innerWidth / 2, top: window.innerHeight / 2, width: 0, height: 0};
    const originX = rect.left + rect.width / 2;
    const originY = rect.top + rect.height / 2;
    const burst = document.createElement('div');
    burst.className = 'theme-burst';
    burst.style.left = originX + 'px';
    burst.style.top = originY + 'px';
    const amount = window.innerWidth < 700 ? 12 : 18;
    for (let i = 0; i < amount; i += 1) {
      const node = document.createElement('span');
      node.textContent = glyphs[Math.floor(Math.random() * glyphs.length)];
      const angle = (Math.PI * 2 * i) / amount + (Math.random() * 0.36 - 0.18);
      const distance = 34 + Math.random() * (window.innerWidth < 700 ? 42 : 68);
      node.style.setProperty('--tx', `${Math.cos(angle) * distance}px`);
      node.style.setProperty('--ty', `${Math.sin(angle) * distance}px`);
      node.style.setProperty('--rot', `${-120 + Math.random() * 240}deg`);
      node.style.animationDelay = `${Math.random() * 0.08}s`;
      burst.appendChild(node);
    }
    document.body.appendChild(burst);
    window.setTimeout(() => burst.remove(), 1100);
  }

  function pulseTheme() {
    body.classList.remove('theme-flash', 'theme-glow');
    void body.offsetWidth;
    body.classList.add('theme-flash', 'theme-glow');
    window.setTimeout(() => body.classList.remove('theme-flash', 'theme-glow'), 900);
  }

  accentInputs.forEach((input) => {
    input.addEventListener('change', () => {
      if (!input.checked) return;
      body.setAttribute('data-accent', input.value);
      emitBurst(input.nextElementSibling || input.parentElement || input);
      pulseTheme();
    });
  });
})();
