/* Vorschaubilder, die nicht laden — Platzhalter statt JSON-als-Bild.

   `/api/faces/…/crop` liefert bei fehlendem Archiv JSON mit 404/503. Ohne
   `onerror` zeigt der Browser das als dasselbe unkenntliche Kreisbild bei
   jeder Person, und ein Cache haelt es fest. Hier: Silhouette, einmal
   eine Meldung, und „erneut versuchen“ holt die Originale neu. */

import { capabilities, forgetCapabilities } from "./capabilities.js";

const FALLBACK =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="32" fill="#2a3140"/>
      <circle cx="32" cy="24" r="12" fill="#6b7384"/>
      <ellipse cx="32" cy="58" rx="20" ry="18" fill="#6b7384"/>
    </svg>`,
  );

const MEDIA_RE = /\/api\/(faces\/[^/]+\/crop|photos\/[^/]+\/thumb)/;

let banner = null;

function isMediaSrc(src) {
  return MEDIA_RE.test(src || "");
}

function bust(src) {
  const u = new URL(src, location.origin);
  u.searchParams.set("_", String(Date.now()));
  return u.pathname + u.search;
}

function showBanner(why) {
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "pv-media-banner";
    banner.className = "pv-media-banner";
    banner.setAttribute("role", "status");
    banner.innerHTML =
      `<span class="pv-media-banner-text"></span>` +
      `<button type="button" class="mini" data-retry>erneut versuchen</button>` +
      `<button type="button" class="mini ghost" data-hide>ausblenden</button>`;
    banner.querySelector("[data-retry]").addEventListener("click", retryMedia);
    banner.querySelector("[data-hide]").addEventListener("click", () => {
      banner.classList.add("hidden");
    });
    const host = document.querySelector("header.top");
    if (host) host.after(banner);
    else document.body.prepend(banner);
  }
  const text = why
    ? `Vorschaubilder nicht ladbar: ${why}`
    : "Vorschaubilder nicht ladbar — Fotoarchiv gerade nicht erreichbar?";
  banner.querySelector(".pv-media-banner-text").textContent = text;
  banner.classList.remove("hidden");
}

async function retryMedia() {
  const btn = banner?.querySelector("[data-retry]");
  if (btn) { btn.disabled = true; btn.textContent = "lädt …"; }
  forgetCapabilities();
  document.querySelectorAll("img.img-missing").forEach((img) => {
    const orig = img.dataset.origSrc;
    if (!orig) return;
    delete img.dataset.broken;
    img.classList.remove("img-missing");
    img.removeAttribute("title");
    img.src = bust(orig);
  });
  const state = await capabilities();
  const archiv = state.features?.archive;
  if (archiv && !archiv.ok) {
    showBanner(archiv.why);
  } else if (banner) {
    banner.classList.add("hidden");
  }
  if (btn) { btn.disabled = false; btn.textContent = "erneut versuchen"; }
}

function onImgError(e) {
  const img = e.target;
  if (!(img instanceof HTMLImageElement)) return;
  const src = img.currentSrc || img.getAttribute("src") || "";
  if (img.dataset.broken || !isMediaSrc(src)) return;
  img.dataset.broken = "1";
  img.dataset.origSrc = src;
  img.classList.add("img-missing");
  img.alt = img.alt || "";
  img.title = "Bild nicht ladbar";
  img.src = FALLBACK;
  showBanner();
}

export function watchMedia() {
  document.addEventListener("error", onImgError, true);
  capabilities().then((state) => {
    const archiv = state.features?.archive;
    if (archiv && !archiv.ok) showBanner(archiv.why);
  });
}
