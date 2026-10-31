(function () {
  const root = document.documentElement;
  try { root.dataset.appearance = localStorage.getItem("dtg-appearance") || "dark"; } catch (_) {}
  document.addEventListener("DOMContentLoaded", () => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "theme-switch";
    const update = () => {
      const light = root.dataset.appearance === "light";
      button.textContent = light ? "☾" : "☀";
      button.setAttribute("aria-label", document.documentElement.lang === "ru" ? "Переключить тему" : "Switch theme");
      button.setAttribute("aria-pressed", String(light));
    };
    button.addEventListener("click", () => {
      root.dataset.appearance = root.dataset.appearance === "light" ? "dark" : "light";
      try { localStorage.setItem("dtg-appearance", root.dataset.appearance); } catch (_) {}
      update();
    });
    update();
    document.body.appendChild(button);
  });
})();
