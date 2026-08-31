import type { Config } from "tailwindcss";

// The theme is REPLACED, not extended, for colour, radius and type scale. That
// is the enforcement mechanism: with `extend`, `bg-gray-50` and `rounded`
// (4px) still resolve, and Slice 1's flat grey boxes come back one careless
// copy-paste at a time. Overridden, those classes produce no CSS at all, so a
// stray palette class is visible on screen the first time it is rendered
// instead of surviving review.
//
// Every value is a var() into app/globals.css, so light/dark switching is the
// cascade doing its job - no `dark:` variant on any component.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    // No `white`/`black` here either: an opaque literal white is wrong in dark
    // mode by construction, and `--surface-container-lowest` is the token that
    // means it.
    colors: {
      transparent: "transparent",
      current: "currentColor",
      inherit: "inherit",
      surface: {
        DEFAULT: "var(--surface)",
        container: {
          DEFAULT: "var(--surface-container)",
          lowest: "var(--surface-container-lowest)",
          low: "var(--surface-container-low)",
          high: "var(--surface-container-high)",
          highest: "var(--surface-container-highest)",
        },
      },
      "on-surface": {
        DEFAULT: "var(--on-surface)",
        variant: "var(--on-surface-variant)",
      },
      outline: {
        DEFAULT: "var(--outline)",
        variant: "var(--outline-variant)",
      },
      primary: {
        DEFAULT: "var(--primary)",
        container: "var(--primary-container)",
      },
      "on-primary": {
        DEFAULT: "var(--on-primary)",
        container: "var(--on-primary-container)",
      },
      error: {
        DEFAULT: "var(--error)",
        container: "var(--error-container)",
      },
      "on-error": {
        DEFAULT: "var(--on-error)",
        container: "var(--on-error-container)",
      },
      scrim: "var(--scrim)",
    },

    // §3 Shape. `rounded` with no suffix is 8px, not Tailwind's 4px, so the
    // laziest possible class is still on the scale.
    borderRadius: {
      none: "0px",
      xs: "4px",
      sm: "8px",
      DEFAULT: "8px",
      md: "12px",
      lg: "16px",
      xl: "28px",
      full: "9999px",
    },

    // §5 Type roles, not t-shirt sizes. Each carries its line-height, and the
    // Korean-safe leading is therefore impossible to drop by writing
    // `text-body` without a matching `leading-*`.
    fontSize: {
      caption: ["12px", { lineHeight: "16px" }],
      label: ["13px", { lineHeight: "18px" }],
      body: ["14px", { lineHeight: "21px" }],
      "body-lg": ["16px", { lineHeight: "26px" }],
      title: ["18px", { lineHeight: "24px" }],
      headline: ["24px", { lineHeight: "32px" }],
      display: ["36px", { lineHeight: "44px" }],
    },

    extend: {
      fontFamily: {
        // The two next/font variables, then the fallback stack from §5. Noto
        // Sans KR sits after Inter so Latin takes Inter's metrics and Hangul
        // falls through to Noto - one family cannot do both well.
        sans: [
          "var(--font-inter)",
          "var(--font-noto-sans-kr)",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Malgun Gothic",
          "sans-serif",
        ],
      },
      // §4. These are the ONLY two shadows in the app.
      boxShadow: {
        menu: "var(--shadow-menu)",
        dialog: "var(--shadow-dialog)",
        none: "none",
      },
      backgroundImage: {
        brand: "var(--gradient-brand)",
      },
      // §7 One curve, three durations. `duration-150/200/250` are already
      // Tailwind defaults; the curve is not.
      transitionTimingFunction: {
        DEFAULT: "cubic-bezier(0.2, 0, 0, 1)",
        standard: "cubic-bezier(0.2, 0, 0, 1)",
      },
      // §6 Sidebar 280px, chat column 768px.
      spacing: {
        sidebar: "280px",
      },
      maxWidth: {
        transcript: "768px",
        // The admin page column at >=1536px, used by .page-shell. Below that
        // the column is max-w-7xl (1280px); this is the one step it takes on a
        // wide desktop, measured at 1920 and 2560 where a 1024px column left
        // 308px and 768px of dead gutter on each side.
        page: "1600px",
        // The readable measure a paragraph keeps inside that 1600px column.
        // Korean at 14/21 runs ~75 characters here; the full column would run
        // ~115, which is past the point the eye finds the next line.
        measure: "56rem",
      },
    },
  },
  plugins: [],
};

export default config;
