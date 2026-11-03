// API keys for the workbench when the server requires them (production). Loaded before app.js.
// Same-origin requests carry the key from this tab's session storage; the first 401 asks for it
// once. Download links are fetched with the key and saved, since a plain link can't send a header.
(() => {
  "use strict";
  const STORE = "workbench-api-key";
  const plainFetch = window.fetch.bind(window);
  const sameOrigin = (url) => new URL(url, location.href).origin === location.origin;
  const key = () => { try { return sessionStorage.getItem(STORE) || ""; } catch { return ""; } };
  const remember = (k) => { try { sessionStorage.setItem(STORE, k); } catch { /* storage blocked */ } };

  // The page loads several things at once: every 401 shares one prompt, then all retry with the key.
  let asking = null;
  function askKey() {
    if (!asking) {
      asking = new Promise((resolve) => setTimeout(() => {
        let entered = null;
        try {
          entered = window.prompt("This workbench needs an API key. Paste the key you were given:");
        } catch {
          // Some embedded browsers block dialogs; the page then shows the server's "API key required" error.
        }
        if (entered && entered.trim()) remember(entered.trim());
        resolve(key());
        setTimeout(() => { asking = null; }, 1000);
      }, 0));
    }
    return asking;
  }

  async function keyed(input, init = {}) {
    const url = typeof input === "string" ? input : input.url;
    if (!sameOrigin(url)) return plainFetch(input, init);
    const send = (k) => {
      const headers = new Headers(init.headers || {});
      if (k) headers.set("X-API-Key", k);
      return plainFetch(input, { ...init, headers });
    };
    const sent = key();
    const response = await send(sent);
    if (response.status !== 401) return response;
    const now = key() !== sent ? key() : await askKey();  // another request may already have the key
    return now && now !== sent ? send(now) : response;
  }
  window.fetch = keyed;

  document.addEventListener("click", async (event) => {
    const link = event.target.closest && event.target.closest("a[download]");
    if (!link || !link.href || !sameOrigin(link.href) || link.dataset.keyed) return;
    event.preventDefault();
    const response = await keyed(link.href);
    if (!response.ok) { window.alert(`Download failed (${response.status}).`); return; }
    const disposition = response.headers.get("Content-Disposition") || "";
    const name = (disposition.match(/filename="([^"]+)"/) || [])[1] || link.getAttribute("download") || "download";
    const blob = URL.createObjectURL(await response.blob());
    const temp = Object.assign(document.createElement("a"), { href: blob, download: name });
    temp.dataset.keyed = "1";
    document.body.append(temp);
    temp.click();
    temp.remove();
    setTimeout(() => URL.revokeObjectURL(blob), 60_000);
  });
})();
