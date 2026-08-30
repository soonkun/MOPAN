"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "mopan-theme";

// "system" is the absence of a stored value, not a third stored string: the
// pre-paint script in public/theme.js only ever sets data-theme for an
// explicit light/dark, and globals.css's prefers-color-scheme block handles
// the rest. Keeping "system" out of storage means the two agree by
// construction rather than by both remembering to special-case it.
const OPTIONS = [
  { value: "light", label: "라이트" },
  { value: "dark", label: "다크" },
  { value: "system", label: "시스템" },
] as const;

type Theme = (typeof OPTIONS)[number]["value"];

export default function ThemeToggle() {
  // Always "system" on the server and on the first client render. Reading
  // localStorage during render would be a hydration mismatch; the DOM is
  // already correct by then anyway - theme.js set data-theme before React
  // existed - so this state only drives which segment looks selected.
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "light" || stored === "dark") setTheme(stored);
    } catch {
      // Private mode / blocked site data. "system" is the right answer there.
    }
  }, []);

  function apply(next: Theme) {
    setTheme(next);
    const root = document.documentElement;
    if (next === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", next);
    try {
      if (next === "system") localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The switch still applies for this page; it just will not survive a
      // reload. Better than refusing to switch at all.
    }
  }

  return (
    // role=group + aria-label, not a radiogroup: these are three buttons with
    // pressed state, and a radiogroup would take arrow-key navigation over
    // from Tab for three items that fit on one line.
    <div
      role="group"
      aria-label="테마"
      className="flex gap-1 rounded-full bg-surface-container p-1"
    >
      {OPTIONS.map((option) => {
        const selected = theme === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={selected}
            onClick={() => apply(option.value)}
            className={`flex-1 rounded-full px-2 py-1 text-caption transition-colors duration-150 ${
              selected
                ? "bg-primary-container font-medium text-on-primary-container"
                : "text-on-surface-variant hover:bg-surface-container-high"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
