// Runtime theme switching. The themes themselves are defined as CSS custom
// property blocks in index.css ([data-theme="…"]); this module only flips the
// attribute on <html> and remembers the choice.

export const THEMES = [
  { id: "dark", label: "Dark", swatch: "#0a0a0c", accent: "#0070f3" },
  { id: "midnight", label: "Midnight", swatch: "#0a0e1c", accent: "#6366f1" },
  { id: "light", label: "Light", swatch: "#ffffff", accent: "#0070f3" },
  { id: "sand", label: "Sand", swatch: "#faf6ec", accent: "#c2410c" },
  { id: "deloitte", label: "Deloitte", swatch: "#ffffff", accent: "#86bc25" },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

const STORAGE_KEY = "nexus-theme";
const DEFAULT_THEME: ThemeId = "dark";

function isThemeId(v: string | null): v is ThemeId {
  return THEMES.some((t) => t.id === v);
}

export function getStoredTheme(): ThemeId {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return isThemeId(v) ? v : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(id: ThemeId): void {
  document.documentElement.setAttribute("data-theme", id);
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Private mode / blocked storage — theme just won't persist.
  }
}

/** Called once at module load in main.tsx, before React renders. */
export function applyStoredTheme(): void {
  applyTheme(getStoredTheme());
}
