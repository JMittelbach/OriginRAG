import base64
import hashlib


APPEARANCE_SCRIPT = r"""
<script>
(() => {
  const key = "originrag.appearance.v1";
  const modes = ["system", "light", "dark", "black"];
  const backgrounds = ["mountain-moon", "lunar-horizon", "city-cat", "digital-dna", "moonlit-garden", "artwork", "slate", "ocean", "forest", "plum"];
  const defaults = {mode: "dark", background: "city-cat"};
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  function normalise(value) {
    return {
      mode: modes.includes(value?.mode) ? value.mode : defaults.mode,
      background: backgrounds.includes(value?.background) ? value.background : defaults.background
    };
  }
  let settings = {...defaults};
  try { settings = normalise(JSON.parse(window.localStorage.getItem(key))); } catch {}
  function apply() {
    document.documentElement.dataset.theme = settings.mode === "system"
      ? (media.matches ? "dark" : "light") : settings.mode;
    document.documentElement.dataset.background = settings.background;
    document.dispatchEvent(new Event("originrag:appearance"));
  }
  window.originragAppearance = Object.freeze({
    get: () => ({...settings}),
    set: (value) => {
      settings = normalise(value);
      apply();
      try {
        window.localStorage.setItem(key, JSON.stringify(settings));
        return true;
      } catch { return false; }
    }
  });
  apply();
  media.addEventListener("change", () => { if (settings.mode === "system") apply(); });
  window.addEventListener("storage", (event) => {
    if (event.key !== key && event.key !== null) return;
    try { settings = normalise(JSON.parse(event.newValue)); } catch { settings = {...defaults}; }
    apply();
  });
  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("appearanceButton");
    const dialog = document.getElementById("appearanceDialog");
    const mode = document.getElementById("appearanceMode");
    const background = document.getElementById("appearanceBackground");
    const note = document.getElementById("appearanceNote");
    const status = document.getElementById("appearanceStatus");
    function sync() {
      mode.value = settings.mode;
      background.value = settings.background;
      background.disabled = settings.mode === "black";
      note.textContent = settings.mode === "black"
        ? "Black mode uses a solid black background."
        : settings.background === "artwork"
          ? "DNA artwork has matching Light and Dark versions."
          : "Wallpapers are softened in Light mode for readability.";
    }
    function save() {
      const persisted = window.originragAppearance.set({mode: mode.value, background: background.value});
      status.textContent = persisted ? "Saved in this browser." : "Applied for this visit; browser storage is unavailable.";
    }
    button.addEventListener("click", () => {
      sync();
      dialog.showModal();
      button.setAttribute("aria-expanded", "true");
      mode.focus();
    });
    dialog.addEventListener("close", () => {
      button.setAttribute("aria-expanded", "false");
      button.focus();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
    mode.addEventListener("change", save);
    background.addEventListener("change", save);
    document.addEventListener("originrag:appearance", sync);
    sync();
  });
})();
</script>
"""

APPEARANCE_SCRIPT_HASH = base64.b64encode(hashlib.sha256(
    APPEARANCE_SCRIPT.split('<script>', 1)[1].split('</script>', 1)[0].encode()
).digest()).decode()

APPEARANCE_DIALOG = r"""
<dialog id="appearanceDialog" class="appearance-panel" aria-labelledby="appearanceTitle" aria-describedby="appearanceDescription">
  <form method="dialog" class="dialog-dismiss-form"><button class="dialog-close" type="submit" aria-label="Close appearance" title="Close appearance">×</button></form>
  <h2 id="appearanceTitle">Appearance</h2>
  <p id="appearanceDescription">Make this workspace yours.</p>
  <div class="appearance-fields">
    <label for="appearanceMode">Mode</label>
    <select id="appearanceMode">
      <option value="system">Use system setting</option>
      <option value="light">Light</option>
      <option value="dark">Dark</option>
      <option value="black">Black</option>
    </select>
    <label for="appearanceBackground">Background</label>
    <select id="appearanceBackground" aria-describedby="appearanceNote">
      <optgroup label="Wallpapers">
        <option value="city-cat">City cat (default)</option>
        <option value="mountain-moon">Mountains &amp; moon</option>
        <option value="lunar-horizon">Lunar horizon</option>
        <option value="digital-dna">Digital DNA</option>
        <option value="moonlit-garden">Moonlit forest</option>
        <option value="artwork">DNA artwork</option>
      </optgroup>
      <optgroup label="Plain colours">
        <option value="slate">Slate</option>
        <option value="ocean">Ocean blue</option>
        <option value="forest">Forest green</option>
        <option value="plum">Plum</option>
      </optgroup>
    </select>
  </div>
  <p id="appearanceNote">Artwork and colours adapt to Light or Dark mode.</p>
  <p id="appearanceStatus" role="status" aria-live="polite">Changes are saved in this browser.</p>
</dialog>
"""

APPEARANCE_STYLE = r"""
    :root {
      --hue: 205;
      --bg: hsl(var(--hue), 32%, 6%);
      --sidebar: hsl(var(--hue), 25%, 8%);
      --panel: hsl(var(--hue), 25%, 10%);
      --panel-2: hsl(var(--hue), 25%, 13%);
      --panel-3: hsl(var(--hue), 25%, 18%);
      --line: hsl(var(--hue), 20%, 26%);
      --line-strong: hsl(var(--hue), 20%, 38%);
      --muted: #acbdc5;
      --code: var(--panel-2);
      --on-teal: #04100f;
      --veil: 4, 11, 16;
      --veil-start: .30;
      --veil-end: .06;
      --artwork: url("/assets/web/originrag-background-dark.png?v=20260907-1");
    }
    :root[data-background="slate"] { --hue: 215; }
    :root[data-background="ocean"] { --hue: 225; }
    :root[data-background="forest"] { --hue: 155; }
    :root[data-background="plum"] { --hue: 280; }
    :root[data-theme="light"] {
      color-scheme: light;
      --bg: hsl(var(--hue), 28%, 96%);
      --sidebar: hsl(var(--hue), 22%, 94%);
      --panel: #ffffff;
      --panel-2: hsl(var(--hue), 24%, 93%);
      --panel-3: hsl(var(--hue), 24%, 88%);
      --line: hsl(var(--hue), 16%, 75%);
      --line-strong: hsl(var(--hue), 18%, 52%);
      --text: #172b35;
      --muted: #4b616d;
      --soft: #28414c;
      --accent: #172b35;
      --accent-2: #18596f;
      --teal: #087568;
      --teal-soft: rgba(8, 117, 104, .09);
      --amber: #865300;
      --danger: #b42332;
      --on-teal: #ffffff;
      --veil: 246, 248, 250;
      --veil-start: .92;
      --veil-end: .86;
      --artwork: url("/assets/web/originrag-background-light.png?v=20260907-1");
      --shadow: 0 12px 36px rgba(20, 40, 50, .08);
    }
    :root[data-theme="black"] {
      color-scheme: dark;
      --bg: #000000;
      --sidebar: #080808;
      --panel: #101010;
      --panel-2: #171717;
      --panel-3: #242424;
      --line: #343434;
      --line-strong: #555555;
      --code: #1b1b1b;
    }
    :root[data-background="artwork"] { --veil-start: .30; --veil-end: .06; }
    :root[data-background="mountain-moon"] { --artwork: url("/assets/web/originrag-wallpaper-mountain-moon.png?v=20260907-1"); }
    :root[data-background="lunar-horizon"] { --artwork: url("/assets/web/originrag-wallpaper-lunar-horizon.png?v=20260907-1"); }
    :root[data-background="city-cat"] { --artwork: url("/assets/web/originrag-wallpaper-city-cat.png?v=20260907-2"); }
    :root[data-background="digital-dna"] { --artwork: url("/assets/web/originrag-wallpaper-digital-dna.png?v=20260907-1"); }
    :root[data-background="moonlit-garden"] { --artwork: url("/assets/web/originrag-wallpaper-moonlit-forest.png?v=20260907-2"); }
    body::before { background: var(--bg); }
    main { background: var(--bg); }
    :root:is([data-background="artwork"], [data-background="mountain-moon"],
      [data-background="lunar-horizon"], [data-background="city-cat"],
      [data-background="digital-dna"], [data-background="moonlit-garden"]):not([data-theme="black"]) main {
      background: linear-gradient(90deg, rgba(var(--veil), var(--veil-start)), rgba(var(--veil), var(--veil-end))),
        var(--artwork) right center / cover no-repeat;
    }
    aside { background: var(--sidebar); }
    header { background: var(--bg); }
    .header-left { flex-shrink: 0; }
    .header-actions { min-width: 0; flex-wrap: wrap; justify-content: flex-end; }
    .bubble, .source-list li, .paper-table-wrap, .citation-trail,
    .advanced, .queue-summary, .hero-signal, .pill, .source-legend,
    .assurance-panel, .study-audit-panel, .study-audit-section,
    .claim-trace-panel, .source-chain-node, details.sources-box,
    .profile-panel, .about-panel, .queue-panel, .library-browser-panel,
    .paper-upload-panel, .library-stats-popover, .library-stats-donut-center {
      background: var(--panel);
    }
    .user .bubble, .composer-inner, .header-button, .library-stats-button,
    .queue-detail-row, .library-results, .library-sync-status { background: var(--panel-2); }
    .profile-panel, .about-panel, .queue-panel, .library-browser-panel,
    .paper-upload-panel, .session-menu { border-color: var(--line); }
    .session-menu { background: var(--panel); }
    .nav-button.primary:hover, .section-toggle:hover, .session-row:hover,
    .session-row.current, .session-row.menu-open, .session-menu-button:hover,
    .session-menu-item:hover, .library-stats-button:hover,
    .library-stats-button[aria-expanded="true"] { background: var(--panel-3); border-color: var(--line); }
    select, input, #profileText, .paper-upload-doi input, .library-search {
      background: var(--panel-2); color: var(--text);
    }
    select:disabled { opacity: .6; cursor: not-allowed; }
    textarea { color: var(--text); }
    input::placeholder, textarea::placeholder { color: var(--muted); opacity: 1; }
    .markdown h3, code { color: var(--soft); }
    .citation-context { color: var(--muted); }
    .assistant.job-failed .bubble, .session-menu-item.danger { color: var(--danger); }
    .skip-link { color: var(--on-teal); }
    #send { color: var(--bg); }
    .composer { background: linear-gradient(180deg, transparent, var(--bg) 24%); }
    .appearance-panel {
      width: min(420px, calc(100vw - 32px));
      max-height: calc(100dvh - 32px);
      overflow: auto;
      padding: 24px;
      border: 1px solid var(--line);
      border-radius: 18px;
      color: var(--text);
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    .appearance-panel::backdrop { background: rgba(0, 0, 0, .5); backdrop-filter: blur(6px); }
    .appearance-panel h2 { margin: 0; font-size: 23px; }
    .appearance-panel p { margin: 8px 0 20px; color: var(--muted); }
    .appearance-fields { display: grid; gap: 8px; }
    .appearance-fields label { font-weight: 650; color: var(--soft); }
    .appearance-fields select { min-height: 44px; margin-bottom: 10px; }
    #appearanceNote, #appearanceStatus { font-size: 12px; margin-bottom: 10px; }
    .appearance-actions { display: flex; justify-content: flex-end; margin-top: 22px; }
    .appearance-actions button { width: auto; padding: 0 22px; }
    .assistant .markdown {
      padding: clamp(20px, 3vw, 32px);
      font-size: 15px;
      line-height: 1.75;
      overflow-wrap: anywhere;
      box-shadow: none;
    }
    .markdown h2 { font-size: 19px; margin: 26px 0 10px; line-height: 1.4; }
    .markdown h3 { margin: 22px 0 8px; line-height: 1.45; }
    .markdown p { margin: 0 0 16px; }
    .markdown ul, .markdown ol { margin: 0 0 18px; padding-left: 24px; }
    .markdown li { margin: 9px 0; padding-left: 3px; }
    .markdown > :first-child { margin-top: 0; }
    .markdown > :last-child { margin-bottom: 0; }
    .inline-citation { font-weight: 500; }
    .result-meta { gap: 0; margin-top: 2px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel); overflow: hidden; }
    .result-meta > .assurance-panel, .result-meta > .sources-box,
    .result-meta > .claim-trace-panel, .result-meta > .answer-details {
      margin: 0; border: 0; border-radius: 0; background: transparent;
    }
    .result-meta > * + * { border-top: 1px solid var(--line); }
    .result-meta > .sources-box, .result-meta > .claim-trace-panel,
    .answer-details { padding: 13px 16px; }
    .result-meta summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--soft); }
    .result-meta summary:focus-visible { outline: 2px solid var(--teal); outline-offset: 3px; }
    .assurance-head { padding: 14px 16px; align-items: center; flex-wrap: wrap; gap: 8px 16px; }
    .assurance-title { flex: 1 1 220px; }
    .assurance-title strong::before { content: "▸"; display: inline-block; width: 16px; color: var(--muted); }
    .assurance-panel[open] .assurance-title strong::before { content: "▾"; }
    .assurance-title span { font-size: 11px; line-height: 1.5; }
    .assurance-status { white-space: normal; }
    .assurance-panel .study-audit-action { padding: 0 16px 14px; }
    .answer-warnings { padding: 12px 16px; color: var(--amber); font-size: 12px; line-height: 1.6; }
    .answer-warnings p { margin: 0; }
    .answer-warnings p + p { margin-top: 6px; }
    .answer-details .result-summary { margin-top: 12px; gap: 6px; }
    .answer-details .pill { border: 0; border-radius: 5px; font-size: 11px; }
    .source-list li { border: 0; border-radius: 0; padding: 14px 0 14px 32px; background: transparent; }
    .source-list li + li { border-top: 1px solid var(--line); }
    .source-list li::before { left: 0; top: 16px; }
    .source-title { display: block; font-size: 13px; line-height: 1.6; }
    .source-journal { display: block; font-size: 11px; margin-top: 3px; }
    .reference-details { margin-top: 8px; }
    .reference-details summary, .source-legend summary { color: var(--muted); font-size: 11px; font-weight: 500; }
    .reference-details[open] summary { margin-bottom: 8px; }
    .source-legend { border: 0; padding: 12px 0 0; background: transparent; }
    .source-legend p { margin: 8px 0 0; }
"""


def with_appearance(html: str) -> str:
    return (html.replace("<style>", APPEARANCE_SCRIPT + "<style>", 1)
            .replace("</style>", APPEARANCE_STYLE + "</style>", 1)
            .replace("<body>", "<body>" + APPEARANCE_DIALOG, 1))
