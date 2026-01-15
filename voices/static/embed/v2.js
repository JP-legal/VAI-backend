(() => {
  const SCRIPT = document.currentScript;
  const API_BASE = new URL("/", SCRIPT.src).origin;
  const EMBED_ID = SCRIPT.dataset.embedId;

  if (!EMBED_ID) {
    console.error("Missing data-embed-id attribute on script tag");
    return;
  }

  // Allowed fonts and their stacks
  const FONT_STACKS = {
    Arial: 'Arial, Helvetica, sans-serif',
    Inter: '"Inter", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif',
    Roboto: 'Roboto, system-ui, -apple-system, "Segoe UI", "Helvetica Neue", Arial, "Noto Sans", "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif',
    Poppins: '"Poppins", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif',
  };

  // Google Fonts CSS URLs for the three web fonts
  const GF_URLS = {
    Inter: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    Roboto: "https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap",
    Poppins: "https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap",
  };

  function normalizeFontName(name) {
    const n = String(name || "").trim();
    const title = n.charAt(0).toUpperCase() + n.slice(1).toLowerCase();
    return ["Arial", "Inter", "Roboto", "Poppins"].includes(title) ? title : "Arial";
  }

  // Load the selected Google Font (if applicable) at document level
  function ensureFontLoaded(fontName) {
    if (fontName === "Arial") return Promise.resolve(); // system font

    const url = GF_URLS[fontName];
    if (!url) return Promise.resolve();

    // Add preconnects once
    if (!document.getElementById("gf-preconnect-1")) {
      const p1 = document.createElement("link");
      p1.id = "gf-preconnect-1";
      p1.rel = "preconnect";
      p1.href = "https://fonts.googleapis.com";
      document.head.appendChild(p1);
    }
    if (!document.getElementById("gf-preconnect-2")) {
      const p2 = document.createElement("link");
      p2.id = "gf-preconnect-2";
      p2.rel = "preconnect";
      p2.href = "https://fonts.gstatic.com";
      p2.crossOrigin = "anonymous";
      document.head.appendChild(p2);
    }

    // Add the stylesheet link once per family
    const linkId = `gf-${fontName.toLowerCase()}`;
    if (!document.getElementById(linkId)) {
      const l = document.createElement("link");
      l.id = linkId;
      l.rel = "stylesheet";
      l.href = url;
      document.head.appendChild(l);
    }

    // Try to wait until the font is available (don’t block forever)
    if (document.fonts && document.fonts.load) {
      const probe = `"${fontName}"`;
      const loadOne = document.fonts.load(`1em ${probe}`);
      const timeout = new Promise((res) => setTimeout(res, 800));
      return Promise.race([loadOne, timeout]).then(() => undefined);
    }
    return Promise.resolve();
  }

  function loadEmbed() {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector('script[src*="@elevenlabs/convai-widget-embed"]');
      if (existing) {
        if (existing.dataset.loaded === "1") return resolve();
        existing.addEventListener("load", () => { existing.dataset.loaded = "1"; resolve(); }, { once: true });
        existing.addEventListener("error", () => reject(), { once: true });
        return;
      }
      const s = document.createElement("script");
      s.src = "https://unpkg.com/@elevenlabs/convai-widget-embed";
      s.async = true;
      s.addEventListener("load", () => { s.dataset.loaded = "1"; resolve(); }, { once: true });
      s.addEventListener("error", () => reject(), { once: true });
      document.head.appendChild(s);
    });
  }

  function injectStyles(widget, color, fontStack) {
    const root = widget.shadowRoot;
    if (!root) return;

let css = `
:host, :root{
  --el-base-primary: #212121!important
  --el-base-active: #F1F5F9 !important;
  font-family: ${fontStack} !important;
}
.bg-base-active .text-base-primary{
  border:1px solid !important;
  border-color: ${color};
}
.bg-accent .text-accent-primary{
  border: 1px solid !important;
  border-color: ${color};
  color: ${color};
}
.bg-accent{
  background-color: ${color} !important;
}
.\\[field-sizing\\:content\\] .rounded-input{
  border-radius: 14px !important;
}
button, [role=button] {
  box-shadow: 0px 2px 2px 0px #00000024 !important;
  color: ${color};
}
.shadow-lg, .shadow-md{
  box-shadow: 0 4px 4px -1px #0C0C0D0D !important;
}
`.trim();

// ✅ Add this extra style only if the color is pure white
if (color.toUpperCase() === "#FFFFFF") {
  css += `
.text-accent-primary {
  color: #212121 !important;
}
  `;
}

    try {
      if (root.adoptedStyleSheets !== undefined) {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(css);
        root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
      } else {
        const style = document.createElement("style");
        style.textContent = css;
        root.appendChild(style);
      }
    } catch {
      const style = document.createElement("style");
      style.textContent = css;
      root.appendChild(style);
    }
  }

  fetch(`${API_BASE}/api/embed/${EMBED_ID}/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include"
  })
    .then(r => r.json())
    .then(data => {
      const token = data?.agentId;
      if (!token) throw new Error("No agentId in response");

      // Only allow the 4 fonts
      const chosen = normalizeFontName(data?.fontFamily || SCRIPT.dataset.fontFamily || "Arial");
      const color  = data?.themeColor || SCRIPT.dataset.themeColor || "#fd0000";
      const fontStack = FONT_STACKS[chosen];

      const widget = document.createElement("elevenlabs-convai");
      widget.setAttribute("agent-id", token);
      document.body.appendChild(widget);

      // Load font + widget script, then style
      Promise.all([ensureFontLoaded(chosen), loadEmbed()])
        .then(() => customElements.whenDefined("elevenlabs-convai"))
        .then(() => requestAnimationFrame(() => injectStyles(widget, color, fontStack)))
        .catch(() => { /* ignore styling failures; widget still works */ });
    })
    .catch(() => {
      console.error("Failed to fetch agentId token");
    });
})();
