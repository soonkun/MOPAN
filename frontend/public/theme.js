// Loaded from <head> as a plain, parser-blocking <script src>: it runs before
// the browser lays out or paints anything, which is the whole point. React
// hydrating later would be far too late - the user would see one frame of the
// wrong theme on every reload.
//
// It is a file rather than an inline dangerouslySetInnerHTML string because
// this app has a hard no-dangerouslySetInnerHTML rule (see the citation
// rendering in components/chat/MessageBubble.tsx for why), and a static
// exception is still one more place a reviewer has to reason about.
//
// "system" and a missing key are the same thing: no attribute, so the
// prefers-color-scheme block in globals.css decides.
(function () {
  try {
    var theme = localStorage.getItem("mopan-theme");
    if (theme === "dark" || theme === "light") {
      document.documentElement.setAttribute("data-theme", theme);
    }
  } catch (e) {
    // Private mode, blocked site data. The system preference still applies.
  }
})();
