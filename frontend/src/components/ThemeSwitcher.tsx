import { useEffect, useRef, useState } from "react";
import { Palette, Check } from "lucide-react";
import { THEMES, applyTheme, getStoredTheme, type ThemeId } from "../theme";

export function ThemeSwitcher() {
  const [theme, setTheme] = useState<ThemeId>(() => getStoredTheme());
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  const select = (id: ThemeId) => {
    applyTheme(id);
    setTheme(id);
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Change theme"
        aria-expanded={open}
        title="Theme"
        className="p-1.5 text-base-400 hover:text-base-200 hover:bg-base-800/80 rounded-lg transition-colors"
      >
        <Palette className="w-5 h-5" />
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1.5 w-44 bg-base-800 border border-base-700/60 rounded-xl shadow-2xl shadow-black/30 z-30 overflow-hidden animate-scale-in origin-top-right py-1">
          {THEMES.map((t) => (
            <button
              key={t.id}
              onClick={() => select(t.id)}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-base-200 hover:bg-base-700/60 transition-colors duration-100"
            >
              <span
                className="w-4 h-4 rounded-full border border-base-600 flex-shrink-0"
                style={{
                  background: `linear-gradient(135deg, ${t.swatch} 50%, ${t.accent} 50%)`,
                }}
                aria-hidden
              />
              <span className="flex-1 text-left">{t.label}</span>
              {theme === t.id && <Check className="w-3.5 h-3.5 text-accent-light" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
