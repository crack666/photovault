/* Das Atlas-Modell -- ohne Browser.

   8.653 Zeilen JavaScript hatten keinen einzigen Test. Der Atlas ist das
   Alleinstellungsmerkmal, und was hier steht, wurde in einer Woche zweimal
   von Hand im Browser geprueft: die Anordnungen, der Tausch der
   Kontinent-Saetze, die Schwerpunkte, die Titel. Von Hand geprueft heisst:
   naechste Woche bricht es still.

   `model.js` ist DOM-frei und braucht nur `fetch`. Der wird hier durch
   eine winzige, erfundene atlas.json ersetzt -- sechs Punkte, zwei
   visuelle Kontinente, zwei Themen-Schubladen. Gepreuft wird der echte
   Einstieg `loadAtlas()`, nicht einzelne Hilfsfunktionen: so faellt auch
   auf, wenn die Verdrahtung dazwischen bricht.

   Laeuft mit dem eingebauten Testlaeufer, ohne Abhaengigkeit:
       node --test tests/js
   und ueber pytest (tests/test_js.py), wenn node erreichbar ist. */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { FLAG, loadAtlas, spreadPoint, termsOf } from "../../web/static/atlas/model.js";

/* Sechs Punkte. Visuell: 0-2 links (Kontinent 0), 3-5 rechts (Kontinent 1).
   Thematisch anders geschnitten: 0,3 sind Schublade 0, der Rest Schublade 1.
   Genau diese Kreuzung ist der Fall, den ein Tausch der Saetze richtig
   halten muss. */
function karte({ mitThemen = true, mitTiteln = true } = {}) {
  const raw = {
    version: 2, built_at: "2026-09-12T00:00:00+00:00", space: "clip", n: 6,
    channels: ["camera"], persons: [], pe: [[], [], [], [], [], []],
    events: [], ev: [0, 0, 0, 0, 0, 0], root: "/mnt/photo", spaces: ["Fotos"],
    sp: [0, 0, 0, 0, 0, 0], tags: [], tg: [[], [], [], [], [], []],
    ids: ["a", "b", "c", "d", "e", "f"],
    x: [0.1, 0.1, 0.2, 0.8, 0.9, 0.9],
    y: [0.5, 0.6, 0.5, 0.5, 0.6, 0.5],
    // Tage seit 1970: 2020 und 2024, ein Punkt ohne Datum.
    t: [18300, 18301, 19800, 19801, -1, 19802],
    cl: [0, 0, 0, 1, 1, 1],
    ch: [0, 0, 0, 0, 0, 0],
    st: [-1, -1, -1, -1, -1, -1],
    fl: [0, 0, FLAG.VIDEO, 0, 0, 0],
    fc: [0, 0, 0, 0, 0, 0],
    clusters: [
      { i: 0, terms: ["wiese", "garten"], cap_share: 1, from_tags: false, n: 3, x: 0.13, y: 0.53, cover: "a", years: [] },
      { i: 1, terms: ["strand", "meer"], cap_share: 1, from_tags: false, n: 3, x: 0.87, y: 0.53, cover: "d", years: [] },
    ],
  };
  if (mitTiteln) {
    raw.clusters[0].title = "Freizeit im Freien";
    raw.clusters[1].title = "Strandtage";
  }
  if (mitThemen) {
    raw.themes = {
      k: 2,
      x: [0.5, 0.2, 0.2, 0.5, 0.8, 0.8],
      y: [0.1, 0.9, 0.9, 0.1, 0.9, 0.9],
      cl: [0, 1, 1, 0, 1, 1],
      clusters: [
        { i: 0, terms: ["silvester"], title: "Silvester", cap_share: 1, from_tags: false, n: 2, x: 0.5, y: 0.1, cover: "a", years: [] },
        { i: 1, terms: ["kinder"], title: null, cap_share: 1, from_tags: false, n: 4, x: 0.5, y: 0.9, cover: "b", years: [] },
      ],
    };
  }
  return raw;
}

function faelscheFetch(raw) {
  globalThis.fetch = async () => ({ ok: true, json: async () => raw });
}

describe("loadAtlas: zwei Kontinent-Saetze", () => {
  beforeEach(() => faelscheFetch(karte()));

  it("startet mit dem visuellen Satz", async () => {
    const m = await loadAtlas();
    assert.equal(m.clusterSetName, "bedeutung");
    assert.deepEqual([...m.cl], [0, 0, 0, 1, 1, 1]);
    assert.equal(m.clusters.length, 2);
  });

  it("tauscht cl, clusters und clusterLabel zusammen -- und zurueck", async () => {
    const m = await loadAtlas();
    m.useClusterSet("themen");
    assert.equal(m.clusterSetName, "themen");
    assert.deepEqual([...m.cl], [0, 1, 1, 0, 1, 1]);
    assert.equal(m.clusterLabel[0], "Silvester");
    m.useClusterSet("bedeutung");
    assert.deepEqual([...m.cl], [0, 0, 0, 1, 1, 1]);
    assert.equal(m.clusterLabel[0], "Freizeit im Freien");
  });

  it("ein unbekannter Satz faellt auf den visuellen zurueck", async () => {
    const m = await loadAtlas();
    m.useClusterSet("gibtsnicht");
    assert.equal(m.clusterSetName, "bedeutung");
  });

  it("die Themen-Schwerpunkte rechnen mit dem Themen-Satz, nicht dem visuellen", async () => {
    /* Der subtile Fall. Schublade 0 sind die Punkte 0 und 3 -- links und
       rechts auf der visuellen Karte, aber oben in der Themen-Anordnung.
       Ihr Schwerpunkt muss bei y=0.1 liegen. Mit dem falschen Satz
       (visuell: Punkte 0,1,2) laege er bei y=0.9. */
    const m = await loadAtlas();
    const c = m.layouts.themen.centroids;
    assert.ok(Math.abs(c[0 * 2 + 1] - 0.1) < 1e-6, `y des Themen-Schwerpunkts 0: ${c[1]}`);
    assert.ok(Math.abs(c[0 * 2] - 0.5) < 1e-6);
    // Und der visuelle Schwerpunkt 0 liegt links, wie gehabt.
    const v = m.layouts.bedeutung.centroids;
    assert.ok(v[0] < 0.2, `x des visuellen Schwerpunkts 0: ${v[0]}`);
  });

  it("jede Anordnung weiss, welcher Satz zu ihr gehoert", async () => {
    const m = await loadAtlas();
    assert.equal(m.layouts.bedeutung.clusterSet, "bedeutung");
    assert.equal(m.layouts.zeit.clusterSet, "bedeutung");
    assert.equal(m.layouts.themen.clusterSet, "themen");
  });
});

describe("Titel", () => {
  it("der Titel vom Modell schlaegt die Rangwoerter", async () => {
    faelscheFetch(karte());
    const m = await loadAtlas();
    assert.equal(m.clusterLabel[0], "Freizeit im Freien");
    assert.equal(termsOf(m.clusters[0]), "wiese · garten");
  });

  it("ohne Titel die Rangwoerter, erstes Wort gross", async () => {
    faelscheFetch(karte({ mitTiteln: false }));
    const m = await loadAtlas();
    assert.equal(m.clusterLabel[0], "Wiese · garten");
    m.useClusterSet("themen");
    assert.equal(m.clusterLabel[1], "Kinder");     // title: null -> Rangwort
  });
});

describe("ohne Themen (aeltere Karte)", () => {
  it("laedt, hat keine Themen-Anordnung und stuerzt beim Tausch nicht ab", async () => {
    faelscheFetch(karte({ mitThemen: false }));
    const m = await loadAtlas();
    assert.equal(m.layouts.themen, undefined);
    assert.equal(m.clusterSets.themen, undefined);
    m.useClusterSet("themen");
    assert.equal(m.clusterSetName, "bedeutung");
  });
});

describe("Anordnung Zeit", () => {
  it("ohne Datum ganz links, sonst nach Jahr geordnet", async () => {
    faelscheFetch(karte());
    const m = await loadAtlas();
    const x = m.layouts.zeit.x;
    assert.ok(x[4] < 0.04, `undatiert links: ${x[4]}`);
    assert.ok(x[0] < x[2], "2020 liegt links von 2024");
    assert.ok(x[2] > 0.04, "datiert nicht im Feld der undatierten");
  });
});

describe("Spreizung", () => {
  it("bei 0 unveraendert, sonst zum Schwerpunkt hin", () => {
    assert.equal(spreadPoint(0.3, 0.9, 0), 0.3);
    const gespreizt = spreadPoint(0.3, 0.9, 1);
    assert.ok(gespreizt > 0.3 && gespreizt <= 1, `${gespreizt}`);
  });
});

describe("Flags", () => {
  it("VIDEO ist ein eigenes Bit und passt zu tools/atlas_build.py", async () => {
    assert.equal(FLAG.VIDEO, 1 << 9);
    faelscheFetch(karte());
    const m = await loadAtlas();
    assert.ok(m.fl[2] & FLAG.VIDEO);
    assert.ok(!(m.fl[0] & FLAG.VIDEO));
  });
});
