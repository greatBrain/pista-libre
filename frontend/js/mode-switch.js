// theme.js
const STORAGE_KEY = "theme";
const DARK_CLASS = "dark";


const prefersDarkScheme = () =>
  window.matchMedia("(prefers-color-scheme: dark)").matches;

const getStoredTheme = () => {
  const value = localStorage.getItem(STORAGE_KEY);
  return value === "dark" || value === "light" ? value : null;
};

const resolveTheme = () =>
  getStoredTheme() ?? (prefersDarkScheme() ? "dark" : "light");

const isDarkActive = () =>
  document.documentElement.classList.contains(DARK_CLASS);

function applyTheme(theme, { persist = false } = {}) {
  const isDark = theme === "dark";
  document.documentElement.classList.toggle(DARK_CLASS, isDark);

  if (persist) {
    localStorage.setItem(STORAGE_KEY, isDark ? "dark" : "light");
  }
}
function toggleTheme() {
  const nextTheme = isDarkActive() ? "light" : "dark";
  applyTheme(nextTheme, { persist: true });
}
function initTheme() {
  applyTheme(resolveTheme());

  document.addEventListener("click", (event) => {
    if (event.target.closest(".sun, .moon")) {
      toggleTheme();
    }
  });
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initTheme, { once: true });  
} else {
  initTheme();  
}


