"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "mopan-theme";

// "system" is the absence of a stored value, not a third stored string: the
// pre-paint script in public/theme.js only ever sets data-theme for an
// explicit light/dark, and globals.css's prefers-color-scheme block handles
// the rest. Keeping "system" out of storage means the two agree by
// construction rather than by both remembering to special-case it.
const ORDER = ["system", "light", "dark"] as const;

type Theme = (typeof ORDER)[number];

const LABEL: Record<Theme, string> = {
  system: "시스템 설정",
  light: "라이트 모드",
  dark: "다크 모드",
};

export default function ThemeToggle() {
  // Always "system" on the server and on the first client render. Reading
  // localStorage during render would be a hydration mismatch; the DOM is
  // already correct by then anyway - theme.js set data-theme before React
  // existed - so this state only drives which icon is shown.
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "light" || stored === "dark") setTheme(stored);
    } catch {
      // Private mode / blocked site data. "system" is the right answer there.
    }
  }, []);

  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];

  function apply(value: Theme) {
    setTheme(value);
    const root = document.documentElement;
    if (value === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", value);
    try {
      if (value === "system") localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // The switch still applies for this page; it just will not survive a
      // reload. Better than refusing to switch at all.
    }
  }

  return (
    // One button, top right, out of the way. This was a three-segment
    // 라이트/다크/시스템 control pinned in the sidebar footer, which spent a
    // permanent block of the most-reachable column on a setting that is
    // changed roughly once. Cycling keeps "시스템" reachable, which a plain
    // light/dark switch would strand the moment the user touched it once.
    //
    // The accessible name carries BOTH the current mode and what the click
    // does, because the icon alone cannot say "system" - a monitor glyph is
    // not self-describing, and a screen-reader user gets no icon at all.
    <button
      type="button"
      onClick={() => apply(next)}
      title={`테마: ${LABEL[theme]}`}
      aria-label={`테마: ${LABEL[theme]}. 누르면 ${LABEL[next]}(으)로 바뀝니다.`}
      className="icon-btn fixed right-2 top-2 z-20 bg-surface-container text-on-surface-variant"
    >
      <ThemeIcon theme={theme} />
    </button>
  );
}

// Inline SVG rather than an icon dependency: three glyphs do not justify a
// package, and `currentColor` keeps them on the token like every other icon.
function ThemeIcon({ theme }: { theme: Theme }) {
  const common = {
    width: 20,
    height: 20,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  if (theme === "light") {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  }
  if (theme === "dark") {
    return (
      <svg {...common}>
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <rect x="2.5" y="4" width="19" height="12.5" rx="2" />
      <path d="M8.5 20.5h7M12 16.5v4" />
    </svg>
  );
}
