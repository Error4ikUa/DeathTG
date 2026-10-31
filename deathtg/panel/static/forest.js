(function () {
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.documentElement;
    let language = root.lang === "ru" ? "ru" : "en";
    try { language = localStorage.getItem("dtg-language") || language; } catch (_) {}
    if (language !== "ru" && language !== "en") language = "en";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "language-switch";
    const update = () => {
      button.textContent = language === "ru" ? "EN" : "RU";
      button.setAttribute("aria-label", language === "ru" ? "Switch language to English" : "Переключить язык на русский");
      button.setAttribute("title", button.getAttribute("aria-label"));
    };
    button.addEventListener("click", () => {
      language = language === "ru" ? "en" : "ru";
      try { localStorage.setItem("dtg-language", language); } catch (_) {}
      document.cookie = `dtg_lang=${language}; Path=/; Max-Age=31536000; SameSite=Lax`;
      location.reload();
    });
    update();
    document.body.appendChild(button);
  });
})();
