/* Resolve before paint. One icon button cycles system → light → dark. */
(function () {
  "use strict";
  var root = document.documentElement;
  var media = window.matchMedia("(prefers-color-scheme: dark)");
  var modes = ["auto", "light", "dark"];
  var labels = { auto: "system", light: "light", dark: "dark" };
  var preference = "auto";
  try { preference = localStorage.getItem("pohe-theme") || "auto"; } catch (_) {}
  if (!modes.includes(preference)) { preference = "auto"; }

  function apply() {
    var resolved = preference === "auto" ? (media.matches ? "dark" : "light") : preference;
    root.dataset.theme = resolved;
    root.dataset.themePreference = preference;
    var button = document.getElementById("theme-toggle");
    if (button) {
      var next = modes[(modes.indexOf(preference) + 1) % modes.length];
      var label = "Theme: " + labels[preference] + ". Switch to " + labels[next] + " theme";
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) { meta.content = resolved === "dark" ? "#151b23" : "#f6f8fa"; }
  }

  apply();
  if (media.addEventListener) { media.addEventListener("change", apply); }
  window.addEventListener("storage", function (event) {
    if (event.key === "pohe-theme" || event.key === null) {
      preference = modes.includes(event.newValue) ? event.newValue : "auto";
      apply();
    }
  });
  document.addEventListener("DOMContentLoaded", function () {
    apply();
    var button = document.getElementById("theme-toggle");
    if (!button) { return; }
    button.addEventListener("click", function () {
      preference = modes[(modes.indexOf(preference) + 1) % modes.length];
      try { localStorage.setItem("pohe-theme", preference); } catch (_) {}
      apply();
    });
  });
})();
