(() => {
  const root = document.documentElement;
  const defaultTheme = root.dataset.defaultTheme === "dark" ? "dark" : "light";
  let saved = defaultTheme;
  try {
    saved = localStorage.getItem("sleep-studio-theme") || defaultTheme;
  } catch {
    saved = defaultTheme;
  }
  root.dataset.theme = saved === "dark" ? "dark" : "light";

  const updateButtons = () => {
    const dark = root.dataset.theme === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.textContent = dark ? "☀ Light" : "☾ Dark";
      button.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
      button.setAttribute("aria-pressed", String(dark));
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    updateButtons();
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
        try {
          localStorage.setItem("sleep-studio-theme", root.dataset.theme);
        } catch {
          // The theme still works for this page when browser storage is unavailable.
        }
        updateButtons();
      });
    });
  });
})();
