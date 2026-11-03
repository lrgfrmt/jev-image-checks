#!/usr/bin/env python3
"""Prepare images for run_jev.py. No network, no key. Needs Pillow (pip install pillow).

Usage: python3 prepare.py path/to/images

Image file names must look like A-01-right-side.jpg: a 4-character id, then the intended view, which must
be one of the views in questions.json. For each image this writes, into data/inputs/<id>/:
  whole.jpg            resized so the long side is 1,568 px (the API's limit; it never resizes for you)
  tile-1.jpg..tile-4   four overlapping quarters at full resolution, for the close-up pass
and data/inputs/manifest.json with the sha256 of every file. run_jev.py refuses anything not listed there.
Labels (data/labels.json) are yours to write; see README.md for the format.
"""
import json, re, sys, hashlib, pathlib, os
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ.get("JEV_TEST_DATA") or HERE / "data")
NAME = re.compile(r"^([A-Za-z0-9]-\d\d)-([a-z-]+)\.(jpg|jpeg|png)$")
MAX_SIDE = 1568


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def save(img, path):
    img.save(path, "JPEG", quality=92, progressive=False, optimize=True)


def check(size):
    w, h = size
    assert 280 <= w <= MAX_SIDE and 280 <= h <= MAX_SIDE and w * h <= 2458624, size


def tiles(img):
    w, h = img.size
    tw, th = int(w * 0.55), int(h * 0.55)
    out = []
    for b in [(0, 0, tw, th), (w - tw, 0, w, th), (0, h - th, tw, h), (w - tw, h - th, w, h)]:
        t = img.crop(b)
        if max(t.size) > MAX_SIDE:
            t.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        out.append(t)
    return out


def main(src_dir):
    views = set(json.loads((HERE / "questions.json").read_text())["views"])
    manifest = {"max_side": MAX_SIDE, "images": {}}
    for src in sorted(pathlib.Path(src_dir).iterdir()):
        m = NAME.match(src.name)
        if not m:
            continue
        stem, view = m.group(1), m.group(2)
        if view not in views:
            raise SystemExit(f"{src.name}: view '{view}' is not one of {sorted(views)}")
        out = DATA / "inputs" / stem
        out.mkdir(parents=True, exist_ok=True)
        img = Image.open(src).convert("RGB")
        whole = img.copy()
        whole.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        save(whole, out / "whole.jpg")
        rec = {"source": src.name, "view": view,
               "whole": {"file": "whole.jpg", "size": whole.size, "sha256": sha(out / "whole.jpg")}, "tiles": []}
        for i, t in enumerate(tiles(img), 1):
            save(t, out / f"tile-{i}.jpg")
            rec["tiles"].append({"file": f"tile-{i}.jpg", "size": t.size, "sha256": sha(out / f"tile-{i}.jpg")})
        for part in [rec["whole"]] + rec["tiles"]:
            check(part["size"])
        manifest["images"][stem] = rec
    (DATA / "inputs").mkdir(parents=True, exist_ok=True)
    (DATA / "inputs" / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"prepared {len(manifest['images'])} images into {DATA / 'inputs'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
