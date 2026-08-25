"""Vorschläge, Serien zusammenzulegen — nie still.

Automatisch bleibt die 3-Stunden-Lücke. Was weiter auseinanderliegt, landet
auf einer Karte: Accept / Reject, Name vom Menschen. Manuell clustern ist
ärgerlicher als einmal Reject.

Drei Arten:

* neighbor — zwei Kamera-Serien, 3–12 h Abstand, mit einem Score aus Personen
  und Ordnern. Dump ohne Gesichter: kein Vorschlag.
* unify_folders — eine Serie liegt in mehreren Ordnern.
* timestamp — (fast) dieselbe Aufnahmezeit in verschiedenen Kanälen.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from ingest.events import DEFAULT_GAP, NEIGHBOR_MAX_GAP, is_generic_album, parse_stamp
from ingest.provenance import CAMERA

TIMESTAMP_WINDOW = timedelta(seconds=2)


def _dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    dt, _ = parse_stamp(str(value) if value else None)
    return dt


def _shared_people(a: dict, b: dict) -> list[str]:
    left = [p for p in (a.get("person_names") or []) if p]
    right = set(p for p in (b.get("person_names") or []) if p)
    return [p for p in left if p in right]


def neighbor_score(a: dict, b: dict) -> float | None:
    """Wie stark zwei aufeinanderfolgende Serien zusammengehören.

    None = nicht vorschlagen. Sonst ein Score, höhere Werte zuerst.
    """
    if (a.get("channel") or CAMERA) != (b.get("channel") or CAMERA):
        return None
    if (a.get("channel") or CAMERA) != CAMERA:
        return None
    start_b, end_a = _dt(b.get("start")), _dt(a.get("end"))
    if start_b is None or end_a is None:
        return None
    gap = start_b - end_a
    if gap <= DEFAULT_GAP or gap > NEIGHBOR_MAX_GAP:
        return None

    shared = _shared_people(a, b)
    folders_a = [f for f in (a.get("folders") or []) if not is_generic_album(f)]
    folders_b = [f for f in (b.get("folders") or []) if not is_generic_album(f)]
    dump_only = not folders_a and not folders_b and not shared
    if dump_only:
        return None

    score = 1.0
    if shared:
        score += 2.0 * min(len(shared), 3)
    if folders_a and folders_b and set(folders_a) & set(folders_b):
        score += 1.5
    elif folders_a or folders_b:
        score += 0.4
    # Engere Lücke zählt etwas — 4 h ist plausibler als 11 h.
    hours = gap.total_seconds() / 3600
    score += max(0.0, (12.0 - hours) / 12.0)
    return round(score, 3)


def neighbor_suggestions(events: list[dict], rejected: Iterable[tuple] = ()) -> list[dict]:
    """Paare aufeinanderfolgender Kamera-Serien, nach Score absteigend."""
    blocked = set(_norm_pair(p) for p in rejected)
    camera = [e for e in events if (e.get("channel") or CAMERA) == CAMERA and not e.get("day_level")]
    camera.sort(key=lambda e: _dt(e.get("start")) or datetime.min)
    out = []
    for a, b in zip(camera, camera[1:]):
        pair = _norm_pair((_span(a), _span(b)))
        if pair in blocked:
            continue
        score = neighbor_score(a, b)
        if score is None:
            continue
        out.append({
            "kind": "neighbor",
            "score": score,
            "a": a,
            "b": b,
            "suggested_name": _suggested_name(a, b),
            "shared_people": _shared_people(a, b),
            "gap_minutes": round((_dt(b["start"]) - _dt(a["end"])).total_seconds() / 60),
        })
    out.sort(key=lambda s: -s["score"])
    return out


def unify_folder_suggestions(events: list[dict]) -> list[dict]:
    """Eine Serie, mehrere Ordner — Zusammenlegen vorschlagen, nicht still."""
    out = []
    for ev in events:
        folders = list(dict.fromkeys(ev.get("folders") or []))
        if len(folders) < 2:
            continue
        if (ev.get("channel") or CAMERA) != CAMERA:
            continue
        named = [f for f in folders if not is_generic_album(f)]
        dump = [f for f in folders if is_generic_album(f)]
        # Zwei sprechende Alben (Abiball + Abistreich) sind oft Absicht.
        # Vorschlag vor allem, wenn Dump und Anlass gemischt sind.
        if not dump and len(named) >= 2:
            reason = "split_named"
        elif dump and named:
            reason = "dump_plus_named"
        elif dump:
            reason = "split_dump"
        else:
            continue
        out.append({
            "kind": "unify_folders",
            "score": 2.0 + ev.get("size", 0) / 100,
            "event": ev,
            "folders": folders,
            "reason": reason,
            "suggested_name": ev.get("name") or ev.get("suggested_name") or (named[0] if named else ""),
        })
    out.sort(key=lambda s: -s["score"])
    return out


def timestamp_suggestions(
    photos: list[dict],
    window: timedelta = TIMESTAMP_WINDOW,
    rejected: Iterable[tuple] = (),
) -> list[dict]:
    """Verschiedene Kanäle, (fast) dieselbe Uhrzeit.

    Empfangszeit ist nicht Aufnahmezeit — deshalb nur ein sehr enges Fenster,
    nicht „derselbe Nachmittag".
    """
    timed: list[tuple[datetime, dict]] = []
    for p in photos:
        dt, day_only = parse_stamp(p.get("taken_at") or p.get("date"))
        if dt is None or day_only:
            continue
        timed.append((dt, p))
    timed.sort(key=lambda x: x[0])
    blocked = set(_norm_pair(p) for p in rejected)
    seen: set[tuple] = set()
    out = []
    for i, (dt, photo) in enumerate(timed):
        chan = photo.get("channel") or CAMERA
        for dt2, other in timed[i + 1 :]:
            if dt2 - dt > window:
                break
            other_chan = other.get("channel") or CAMERA
            if other_chan == chan:
                continue
            key = tuple(sorted((photo.get("id") or photo.get("photo_id"),
                                other.get("id") or other.get("photo_id"))))
            if key in seen:
                continue
            seen.add(key)
            pair = _norm_pair(((chan, _iso(dt), _iso(dt)), (other_chan, _iso(dt2), _iso(dt2))))
            if pair in blocked:
                continue
            camera_first = photo if chan == CAMERA else other
            other_first = other if chan == CAMERA else photo
            out.append({
                "kind": "timestamp",
                "score": 3.0,
                "a": camera_first,
                "b": other_first,
                "delta_seconds": abs(int((dt2 - dt).total_seconds())),
                "suggested_name": "",
            })
    out.sort(key=lambda s: -s["score"])
    return out


def _span(ev: dict) -> tuple:
    return (ev.get("channel") or CAMERA, ev.get("start"), ev.get("end"))


def _norm_pair(pair) -> tuple:
    a, b = pair
    return tuple(sorted((tuple(a), tuple(b))))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _suggested_name(a: dict, b: dict) -> str:
    for ev in (a, b):
        if ev.get("name"):
            return ev["name"]
        if ev.get("suggested_name"):
            return ev["suggested_name"]
    return ""
