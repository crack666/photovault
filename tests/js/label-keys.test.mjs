/* Tastenbelegung von "Wer ist das?" (photovault#60) -- ohne Browser.

   Der Pfad, der sonst still bricht: eine Taste, die beim Tippen eines Namens
   losgeht. Das "i" in "Sophie" hat schon einmal die Detailspalte der
   Grossansicht zugeklappt (core/dom.js `isTyping`); hier wuerde ein "J" in
   "Jana" eine ganze Gruppe der falschen Person zuordnen. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { LABEL_KEYS, labelKeyAction } from "../../web/static/faces/keys.js";

const taste = (key, extra = {}) => ({ key, ...extra });

describe("labelKeyAction", () => {
  it("nimmt mit J und Enter an, lehnt mit N ab, blaettert mit Pfeilen", () => {
    assert.equal(labelKeyAction(taste("j")), "confirm");
    assert.equal(labelKeyAction(taste("J")), "confirm");
    assert.equal(labelKeyAction(taste("Enter")), "confirm");
    assert.equal(labelKeyAction(taste("n")), "reject");
    assert.equal(labelKeyAction(taste("s")), "skip");
    assert.equal(labelKeyAction(taste("ArrowRight")), "next");
    assert.equal(labelKeyAction(taste("ArrowLeft")), "prev");
    assert.equal(labelKeyAction(taste("?")), "help");
    assert.deepEqual(labelKeyAction(taste("3")), { pick: 2 });
    assert.equal(labelKeyAction(taste("0")), null);
  });

  it("laesst beim Tippen jede Taste dem Feld", () => {
    for (const k of ["j", "J", "n", "s", "Enter", "ArrowRight", "1", "?"]) {
      assert.equal(labelKeyAction(taste(k), { typing: true, inNameField: true }), null, k);
    }
  });

  it("fuehrt mit Escape nur aus dem Namensfeld heraus", () => {
    assert.equal(labelKeyAction(taste("Escape"), { typing: true, inNameField: true }), "leave-field");
    assert.equal(labelKeyAction(taste("Escape"), { typing: true }), null);
  });

  it("ueberlaesst Enter einem fokussierten Knopf", () => {
    assert.equal(labelKeyAction(taste("Enter"), { onButton: true }), null);
    assert.equal(labelKeyAction(taste("j"), { onButton: true }), "confirm");
  });

  it("greift nicht bei Strg, Alt oder Cmd (Strg+N, Alt+Pfeil gehoeren dem Browser)", () => {
    assert.equal(labelKeyAction(taste("n", { ctrlKey: true })), null);
    assert.equal(labelKeyAction(taste("ArrowLeft", { altKey: true })), null);
    assert.equal(labelKeyAction(taste("j", { metaKey: true })), null);
  });

  it("nennt in der Hilfezeile jede Taste, die eine Aktion hat", () => {
    const text = LABEL_KEYS.map(([k]) => k).join(" ");
    for (const k of ["J", "Enter", "N", "S", "→", "←", "Esc", "?", "1–9"]) {
      assert.ok(text.includes(k), k);
    }
  });
});
