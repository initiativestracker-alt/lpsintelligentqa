#!/usr/bin/env python3
"""Ingest an LPS QA delivery folder into a self-contained ``qa_data/`` directory.

    python qa_ingest.py [<root folder>] [--out qa_data] [--public qa_data]

If <root folder> is omitted, BATCH_ROOT_FOLDER is read from the project .env file.
The root folder holds one folder per batch; a batch folder holds one or more ZIPs
containing the source documents, the extracted output and the coordinate CSV. All the
ZIPs in a batch are unioned and version suffixes (``_1``, ``_2``) resolve to the
highest number at the file level. See ``understanding.md`` for the contract and
``build_plan.md`` for the design.

Output layout::

    qa_data/
      index.json                      batches, folios, entry types, counties
      warnings.json                   everything skipped or ambiguous
      batches/<batch>/<folio>.json    one folio, shaped for LPS_QA_Review.html
      batches/<batch>/<folio>/p1.png  page images
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

RENDER_DPI = 200
POINTS_PER_PX = 72 / RENDER_DPI

SOURCE_EXT = {".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
VALUES_EXT = {".csv"}

HASH_SUFFIX = re.compile(r"-[0-9a-f]{12}$")     # the producer's source_id hash
VERSION_SUFFIX = re.compile(r"_(\d+)$")         # a rerun suffix
PAGE_SUFFIX = re.compile(r"_p(\d+)$")           # one page of an image folio
COORDS_SUFFIX = "-coordinates"

META_COLUMNS = ["source_id", "source_filename", "folio_name", "folio_number", "county", "register_type",
                "first_registration_date", "edition", "opened_date", "prior_folio",
                "entry_id", "parent_entry_id", "record_kind", "register_part", "entry_type",
                "source_pages", "cancellation_status", "review_status", "reason_codes",
                "revision", "scope", "folio_issues", "local_relationships"]

# Synthesised by the exporter for record identity and status, already carried by other columns.
SKIP_FIELDS = {"FOLIO_IMAGE_FILENAME", "FT_ENTRY_TEXT_TYPE_NAME", "IS_ENTRY_STRIKETHROUGH"}

COUNTY_PREFIX = {"antrim": "AN", "armagh": "AM", "down": "DN", "fermanagh": "FE",
                 "londonderry": "LY", "derry": "LY", "tyrone": "TY"}


class Warnings:
    """Everything skipped or ambiguous, so nothing is dropped silently."""

    def __init__(self):
        self.items = []

    def add(self, batch, message, level="warning"):
        self.items.append({"level": level, "batch": batch or "-", "message": message})


# --------------------------------------------------------------------------- naming

def humanise(code):
    """Turn a schema code into a readable label, matching the producer's own rule."""
    text = code.replace("_", " ").replace("FT ", "").replace("LHOLD", "Leasehold").strip()
    return " ".join(word.capitalize() for word in text.split())


def parse_name(filename):
    """Split a delivered filename into ``(key, role, version, page)``.

    ``role`` is ``source`` (a PDF), ``image`` (one page of an image folio), ``values``
    (the extracted CSV) or ``coords`` (the coordinate CSV). ``key`` is the folio's base
    name with every suffix removed.
    """
    stem, ext = Path(filename).stem, Path(filename).suffix.lower()
    role = "other"

    if stem.endswith(COORDS_SUFFIX):
        stem, role = stem[: -len(COORDS_SUFFIX)], "coords"
    if ext in SOURCE_EXT:
        role = "source"
    elif ext in IMAGE_EXT:
        role = "image"
    elif ext in VALUES_EXT and role == "other":
        role = "values"

    stem = HASH_SUFFIX.sub("", stem)

    page = None
    if role == "image":
        match = PAGE_SUFFIX.search(stem)
        if match:
            page, stem = int(match.group(1)), stem[: match.start()]

    version = 0
    match = VERSION_SUFFIX.search(stem)
    if match:
        version, stem = int(match.group(1)), stem[: match.start()]

    return stem, role, version, page


# --------------------------------------------------------------------------- discovery

def discover_batches(root, warnings):
    """Direct child folders of the root, each one a batch. Loose root files are ignored."""
    folders = sorted(p for p in root.iterdir() if p.is_dir())
    loose = sorted(p.name for p in root.iterdir() if p.is_file())
    if loose:
        warnings.add(None, f"{len(loose)} loose file(s) in the root were ignored: "
                           + ", ".join(loose[:5]) + ("…" if len(loose) > 5 else ""))
    return folders


def batch_zips(batch_dir, warnings):
    """Every ZIP in the batch, ordered so that reruns are read last.

    A suffixed ZIP (``delivery_1.zip``) is treated as a *further delivery of the same
    name* rather than a replacement, so nothing is thrown away at this level: all the
    ZIPs are unioned and the version rules are applied to the files. That way a rerun
    containing only a corrected CSV still inherits the page images from the original.
    """
    found, loose = [], []
    for path in sorted(batch_dir.iterdir()):
        if path.is_file() and path.suffix.lower() == ".zip":
            key, _, version, _ = parse_name(path.name)
            found.append((key, version, path))
        elif path.is_file():
            loose.append(path.name)
    if loose:
        warnings.add(batch_dir.name, f"{len(loose)} non-ZIP file(s) ignored: " + ", ".join(loose[:5]))

    grouped = {}
    for key, _version, path in found:
        grouped.setdefault(key, []).append(path.name)
    for key, names in grouped.items():
        if len(names) > 1:
            warnings.add(batch_dir.name, f"{len(names)} deliveries for '{key}' "
                                         f"({', '.join(sorted(names))}); position at file level")

    return [path for _key, _version, path in sorted(found, key=lambda t: (t[0], t[1]))]


def read_zip(path, warnings):
    """Every readable file in the ZIP, keyed by filename with directory depth ignored."""
    files = {}
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = Path(info.filename).name
                if not name or name.startswith("."):
                    continue
                if name in files:
                    warnings.add(path.stem, f"duplicate entry {name} in {path.name}; kept the first")
                    continue
                files[name] = archive.read(info)
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        warnings.add(path.stem, f"{path.name} could not be read and was skipped ({error})")
    return files


def collect_files(zips, warnings, batch_name):
    """Union every ZIP's files, then apply the version rule across the union."""
    union = {}
    for path in zips:
        for name, payload in read_zip(path, warnings).items():
            if name in union and union[name] != payload:
                warnings.add(batch_name, f"{name} appears in more than one ZIP; kept the copy "
                                         f"from {path.name}")
            union[name] = payload

    chosen = {}
    for name in sorted(union):
        key, role, version, page = parse_name(name)
        current = chosen.get((key, role))
        if current is None:
            chosen[(key, role)] = (version, name, page)
        elif version > current[0]:
            warnings.add(batch_name, f"{current[1]} superseded by {name}")
            chosen[(key, role)] = (version, name, page)
        else:
            warnings.add(batch_name, f"{name} superseded by {current[1]}")
    return union, chosen


def family_of(chosen):
    """Group resolved files into folios: one per base name."""
    folios = {}
    for (key, role), (_version, name, page) in chosen.items():
        entry = folios.setdefault(key, {"values": None, "coords": None, "source": None, "images": []})
        if role == "source":
            entry["source"] = name
        elif role == "values":
            entry["values"] = name
        elif role == "coords":
            entry["coords"] = name
        elif role == "image":
            entry["images"].append((page or len(entry["images"]) + 1, name))
    for entry in folios.values():
        entry["images"].sort()
    return folios


# --------------------------------------------------------------------------- CSV helpers

def rows_of(payload):
    return list(csv.DictReader(payload.decode("utf-8-sig", errors="replace").splitlines()))


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def to_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def load_values(payload):
    """Group the extracted CSV rows into entries, in row order."""
    rows = rows_of(payload)
    order = []
    for row in rows:
        entry_id = (row.get("entry_id") or "").strip()
        if entry_id and (not order or order[-1] != entry_id):
            order.append(entry_id)
    entries = []
    for index, entry_id in enumerate(order):
        group = [r for r in rows if (r.get("entry_id") or "").strip() == entry_id]
        entries.append({"id": entry_id, "index": index, "row": group[0]})
    return entries, (rows[0] if rows else {})


def field_columns(header):
    tables = {}
    for column in header:
        if column not in META_COLUMNS and "." in column:
            table, field = column.split(".", 1)
            tables.setdefault(table, []).append(field)
    return tables


def load_coordinates(payload):
    """``(entry_id, table, field)`` -> located record, plus per-entry page and number."""
    index, entries = {}, {}
    if payload is None:
        return index, entries
    for row in rows_of(payload):
        entry_id = (row.get("entry_id") or "").strip()
        page = to_int(row.get("page"))
        points = [to_float(row.get(k)) for k in ("x0_pt", "y0_pt", "x1_pt", "y1_pt")]
        index.setdefault((entry_id, row.get("table", ""), row.get("field", "")), {
            "page": page,
            "basis": (row.get("basis") or "").strip(),
            "quote": (row.get("quote") or "").strip(),
            "box": points if all(p is not None for p in points) else None,
        })
        info = entries.setdefault(entry_id, {"page": None, "number": None})
        info["number"] = info["number"] or (row.get("entry_number") or "").strip() or None
        if info["page"] is None:
            info["page"] = page
    return index, entries


# --------------------------------------------------------------------------- page images

def image_size(path):
    from PIL import Image
    with Image.open(path) as image:
        return image.size


def _render_with_pymupdf(pdf, out_dir):
    try:
        import fitz
    except ImportError:
        return None
    pages = []
    with fitz.open(pdf) as document:
        for number, page in enumerate(document, 1):
            pixmap = page.get_pixmap(dpi=RENDER_DPI)
            target = out_dir / f"p{number}.png"
            pixmap.save(target)
            pages.append((target, pixmap.width * POINTS_PER_PX, pixmap.height * POINTS_PER_PX))
    return pages


def _render_with_pdftoppm(pdf, out_dir):
    if not shutil.which("pdftoppm"):
        return None
    prefix = out_dir / "render"
    subprocess.run(["pdftoppm", "-r", str(RENDER_DPI), "-png", str(pdf), str(prefix)],
                   check=True, capture_output=True, timeout=600)
    pages = []
    for path in sorted(out_dir.glob("render-*.png"),
                       key=lambda p: int(re.search(r"-(\d+)\.png$", p.name).group(1))):
        number = int(re.search(r"-(\d+)\.png$", path.name).group(1))
        target = out_dir / f"p{number}.png"
        path.replace(target)
        width, height = image_size(target)
        pages.append((target, width * POINTS_PER_PX, height * POINTS_PER_PX))
    return pages or None


def placeholder(out_dir, page, width_pt, height_pt):
    """A stand-in page for a folio whose source document was not delivered."""
    target = out_dir / f"p{page}.svg"
    size = max(14.0, width_pt * 0.028)
    target.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_pt:.0f}" height="{height_pt:.0f}" '
        f'viewBox="0 0 {width_pt:.0f} {height_pt:.0f}">'
        f'<rect width="100%" height="100%" fill="#F1F3F6" stroke="#D3D8DF"/>'
        f'<text x="50%" y="48%" text-anchor="middle" font-family="sans-serif" font-size="{size:.0f}" '
        f'fill="#6B7380">Source document not available</text>'
        f'<text x="50%" y="56%" text-anchor="middle" font-family="sans-serif" '
        f'font-size="{size * 0.6:.0f}" fill="#98A1AE">Folio page {page} — fields are shown at '
        f'their recorded positions</text></svg>', encoding="utf-8")
    return target


def build_pages(key, family, payloads, out_dir, public, warnings, batch_name, extent):
    """Write the page images for a folio. Returns ``[(path, url, width_pt, height_pt)]``."""
    out_dir.mkdir(parents=True, exist_ok=True)

    if family["source"]:
        pdf = out_dir / f"_{family['source']}"
        pdf.write_bytes(payloads[family["source"]])
        rendered = _render_with_pymupdf(pdf, out_dir) or _render_with_pdftoppm(pdf, out_dir)
        pdf.unlink(missing_ok=True)
        if rendered:
            return [(path, f"{public}/batches/{batch_name}/{key}/{path.name}", w, h)
                    for path, w, h in rendered]
        warnings.add(batch_name, f"{key}: no PDF renderer found (install pymupdf or poppler); "
                                 f"showing placeholder pages")

    if family["images"]:
        pages = []
        for number, name in family["images"]:
            suffix = Path(name).suffix.lower()
            target = out_dir / f"p{number}{suffix}"
            target.write_bytes(payloads[name])
            width, height = image_size(target)
            pages.append((target, f"{public}/batches/{batch_name}/{key}/{target.name}",
                          width * POINTS_PER_PX, height * POINTS_PER_PX))
        return pages

    # No source document: fall back to the extent of the recorded boxes so the
    # field positions still land somewhere sensible.
    width_pt, height_pt, count = extent
    warnings.add(batch_name, f"{key}: no source document in the delivery; "
                             f"page images are placeholders (hasSource=false)")
    return [(placeholder(out_dir, page, width_pt, height_pt),
             f"{public}/batches/{batch_name}/{key}/p{page}.svg", width_pt, height_pt)
            for page in range(1, count + 1)]


# --------------------------------------------------------------------------- folio assembly

def union(boxes):
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def fraction(points, size_pt):
    if not points or not size_pt[0] or not size_pt[1]:
        return None
    values = [points[0] / size_pt[0], points[1] / size_pt[1],
              points[2] / size_pt[0], points[3] / size_pt[1]]
    return [round(max(0.0, min(1.0, v)), 5) for v in values]


def build_folio(key, family, payloads, batch_name, out_dir, public, warnings):
    """Turn one folio family into the JSON shape the Review page consumes."""
    if family["values"] is None:
        warnings.add(batch_name, f"{key}: no extracted output found; folio skipped")
        return None

    entries, first = load_values(payloads[family["values"]])
    header = list(first.keys())
    tables = field_columns(header)
    coords_index, coords_entries = load_coordinates(
        payloads[family["coords"]] if family["coords"] else None)

    # Page extent from the recorded boxes, used when there is no source document.
    xs = [c["box"][2] for c in coords_index.values() if c["box"]]
    ys = [c["box"][3] for c in coords_index.values() if c["box"]]
    pages_recorded = max([c["page"] for c in coords_index.values() if c["page"]] or [1])
    extent = ((max(xs) * 1.06 if xs else 595.0), (max(ys) * 1.06 if ys else 842.0), pages_recorded)

    folio_name = first.get("folio_name") or key
    county = (first.get("county") or "").strip()
    prefix = COUNTY_PREFIX.get(county.lower(), "")

    pages = build_pages(key, family, payloads, out_dir, public, warnings, batch_name, extent)
    sizes = {(number + 1): (w, h) for number, (_p, _u, w, h) in enumerate(pages)}

    built = []
    for entry in entries:
        row = entry["row"]
        entry_id = entry["id"]
        info = coords_entries.get(entry_id, {})
        fields, boxes = [], []
        for table in sorted(tables):
            try:
                records = json.loads(row.get(table) or "[]")
            except json.JSONDecodeError:
                records = []
            count = len(records) or 1
            for index in range(count):
                for field in tables[table]:
                    if field in SKIP_FIELDS:
                        continue
                    value = row.get(f"{table}.{field}", "")
                    if count > 1:
                        try:
                            values = json.loads(value) if value else []
                        except json.JSONDecodeError:
                            values = []
                        value = values[index] if index < len(values) else ""
                    if value in (None, ""):
                        continue
                    located = coords_index.get((entry_id, table, field), {})
                    page = located.get("page") or info.get("page") or 1
                    box = fraction(located.get("box"), sizes.get(page, (0, 0)))
                    if box:
                        boxes.append(box)
                    fields.append({
                        "label": humanise(field) + (f" #{index + 1}" if count > 1 else ""),
                        "value": str(value),
                        "table": table,
                        "basis": located.get("basis", ""),
                        "quote": located.get("quote", ""),
                        "box": box,
                    })

        try:
            source_pages = json.loads(row.get("source_pages") or "[]")
        except json.JSONDecodeError:
            source_pages = []
        page = info.get("page") or (source_pages[0] if source_pages else 1)
        number = to_int(info.get("number"))
        built.append({
            "id": entry_id,
            "type": row.get("entry_type") or "Entry",
            "page": page,
            "order": number if number is not None else entry["index"] + 1,
            "recordKind": row.get("record_kind") or "entry",
            "inactive": bool((row.get("cancellation_status") or "").strip()),
            "box": union(boxes),
            "fields": fields,
        })

    return {
        "kind": "image",
        "id": f"{prefix}-{folio_name}" if prefix else folio_name,
        "file": key,
        "folio": folio_name,
        "county": county,
        "batch": batch_name,
        "analyst": "Unassigned",
        "sourceId": first.get("source_id") or "",
        "hasSource": bool(family["source"] or family["images"]),
        "pages": [{"img": url, "width": round(w, 1), "height": round(h, 1)} for _p, url, w, h in pages],
        "pageFiles": [f"p{n + 1}" for n in range(len(pages))],
        "entries": sorted(built, key=lambda e: (e["page"], e["order"], e["id"])),
    }


# --------------------------------------------------------------------------- driver

def ingest(root, out_dir, public, warnings):
    batches = discover_batches(root, warnings)
    if not batches:
        warnings.add(None, f"no batch folders found under {root}", level="error")
        return {"generated": None, "root": str(root), "batches": [], "entryTypes": [],
                "counties": [], "totals": {"batches": 0, "folios": 0, "entries": 0}}

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "batches").mkdir(parents=True)

    index_batches, type_counts, counties = [], {}, {}
    total_folios = total_entries = 0

    for folder in batches:
        zips = batch_zips(folder, warnings)
        if not zips:
            warnings.add(folder.name, "no ZIP files found; batch skipped", level="error")
            continue

        payloads, chosen = collect_files(zips, warnings, folder.name)
        families = family_of(chosen)
        folios, batch_entries = [], 0

        for key in sorted(families):
            target_dir = out_dir / "batches" / folder.name / key
            folio = build_folio(key, families[key], payloads, folder.name,
                                target_dir, public, warnings)
            if folio is None:
                continue
            (out_dir / "batches" / folder.name / f"{key}.json").write_text(
                json.dumps(folio, ensure_ascii=False, indent=1), encoding="utf-8")

            for entry in folio["entries"]:
                type_counts[entry["type"]] = type_counts.get(entry["type"], 0) + 1
            if folio["county"]:
                counties[folio["county"]] = counties.get(folio["county"], 0) + 1
            batch_entries += len(folio["entries"])
            folios.append({"id": folio["id"], "folio": folio["folio"], "file": folio["file"],
                           "county": folio["county"], "entries": len(folio["entries"]),
                           "hasSource": folio["hasSource"],
                           "json": f"{public}/batches/{folder.name}/{key}.json"})

        if not folios:
            warnings.add(folder.name, "no usable folios in this batch", level="error")
            continue

        index_batches.append({"name": folder.name, "zips": [z.name for z in zips],
                              "folios": len(folios), "entries": batch_entries,
                              "withSource": sum(1 for f in folios if f["hasSource"]),
                              "withoutSource": sum(1 for f in folios if not f["hasSource"]),
                              "items": folios})
        total_folios += len(folios)
        total_entries += batch_entries

    total = sum(type_counts.values()) or 1
    return {
        "root": str(root),
        "batches": index_batches,
        "counties": sorted(counties) or ["Antrim", "Armagh", "Down", "Fermanagh", "Londonderry", "Tyrone"],
        "entryTypes": [{"name": name, "count": count, "weight": round(count / total, 4)}
                       for name, count in sorted(type_counts.items(), key=lambda kv: -kv[1])],
        "totals": {"batches": len(index_batches), "folios": total_folios, "entries": total_entries},
    }


def env_value(path, name):
    """Read one simple KEY=value setting without requiring python-dotenv."""
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value.partition("#")[0].strip()
    return None


def main(argv=None):
    project_dir = Path(__file__).resolve().parent
    configured_root = os.environ.get("BATCH_ROOT_FOLDER") or env_value(
        project_dir / ".env", "BATCH_ROOT_FOLDER"
    )
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=None,
                        help="folder holding one folder per batch (defaults to BATCH_ROOT_FOLDER in .env)")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("qa_data"),
                        help="where to write the ingested data (default: ./qa_data)")
    parser.add_argument("--public", default=None,
                        help="URL prefix for the generated data (default: the --out folder name)")
    args = parser.parse_args(argv)

    root_value = args.root or configured_root
    if not root_value:
        parser.error("provide <root folder> or set BATCH_ROOT_FOLDER in the project .env file")
    root = Path(root_value).expanduser()
    if args.root is None and not root.is_absolute():
        root = project_dir / root
    root = root.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a folder", file=sys.stderr)
        return 2

    out_dir = args.out.expanduser().resolve()
    public = args.public or out_dir.name
    warnings = Warnings()

    index = ingest(root, out_dir, public, warnings)
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "warnings.json").write_text(
        json.dumps({"count": len(warnings.items), "items": warnings.items},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"batches  {index['totals']['batches']}")
    print(f"folios   {index['totals']['folios']}")
    print(f"entries  {index['totals']['entries']}")
    print(f"types    {len(index['entryTypes'])}")
    print(f"warnings {len(warnings.items)}")
    print(f"written  {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
