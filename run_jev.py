#!/usr/bin/env python3
"""Run Jev (TypeSafe) on a set of generated product images. Standard library only.

Three ways of asking, one call per image each:
  whole : the image (long side 1,568 px) + every defect question in questions.json
  tiles : the same + four full-resolution close-up quarters (does detail on small parts help?)
  view  : the image only + one choice question: which camera angle is this?

The API key is read from JEV_API_KEY (or TYPESAFE_API_KEY). If neither is set and this is a terminal,
it is asked for with hidden typing. It is never printed or saved.

Usage:
  python3 prepare.py path/to/images      resize the images and write data/inputs/ (needs Pillow)
  python3 run_jev.py --dry-run           no network: builds every request, checks inputs, estimates cost
  python3 run_jev.py --dev               3 images x 3 ways (9 calls): checks the wiring, measures real tokens
  python3 run_jev.py                     every image x 3 ways
  add --cap 5.00 to change the spending cap in US dollars (default 5.00)
Then: python3 score.py
Outputs go to data/runs/: <prefix>outputs.jsonl (one line per call) and <prefix>run-log.json.
"""
import json, os, sys, time, base64, hashlib, random, pathlib, statistics, datetime, getpass
import urllib.request, urllib.error

HERE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ.get("JEV_TEST_DATA") or HERE / "data")
URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"                  # pinned: an alias change cannot move the results
PRICE_PER_M_INPUT = 0.042             # USD per million input tokens; images billed as input tokens; output free
DEV_IMAGES = ["A-01", "B-05", "C-10"]  # fixed before any call; change to suit your own set
ARMS = ["whole", "tiles", "view"]

Q = json.loads((HERE / "questions.json").read_text())


def manifest():
    return json.loads((DATA / "inputs" / "manifest.json").read_text())


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load_image(stem, part):
    """Content-origin filter: only files listed in the manifest, with matching hashes, are ever sent."""
    rec = manifest()["images"][stem]
    entry = rec["whole"] if part == "whole.jpg" else next(t for t in rec["tiles"] if t["file"] == part)
    data = (DATA / "inputs" / stem / part).read_bytes()
    if sha(data) != entry["sha256"]:
        raise SystemExit(f"Refusing {stem}/{part}: hash does not match the manifest.")
    return data, entry["size"]


def asset(data):
    ref = "img-" + sha(data)[:20]
    return ref, {"type": "image", "format": "jpg", "data_base64": base64.b64encode(data).decode()}


def build(stem, arm):
    ctx = Q["context"]
    whole, size = load_image(stem, "whole.jpg")
    ref, a = asset(whole)
    assets, sizes = {ref: a}, [size]
    if arm == "view":
        opts = list(Q["view_question"]["options"])
        random.Random(stem).shuffle(opts)       # Jev 1.13 was documented to lean toward the first option
        state = {"product": ctx["product"], "sides": ctx["sides"], "photo": ref}
        questions = {"view": {"type": "choice", "instructions": Q["view_question"]["instructions"],
                              "criteria": {o: Q["views"][o] for o in opts}}}
        return {"model": MODEL, "state": state, "questions": questions, "assets": assets}, sizes
    view = manifest()["images"][stem]["view"]
    state = {"product": ctx["product"], "sides": ctx["sides"], "rule": ctx["rule"],
             "intended_view": Q["views"][view], "photo": ref}
    img = "`photo`"
    if arm == "tiles":
        refs = []
        for i in range(1, 5):
            d, s = load_image(stem, f"tile-{i}.jpg")
            r, a2 = asset(d)
            assets[r] = a2
            refs.append(r)
            sizes.append(s)
        state["close_ups"] = refs
        state["close_ups_note"] = ("close_ups are four overlapping enlarged quarters of photo, in this order: "
                                   "top-left, top-right, bottom-left, bottom-right. Use them to judge small parts.")
        img = "`photo` (with its `close_ups`)"
    questions = {}
    for qid, q in Q["defect_questions"].items():
        item = {"type": "noul", "instructions": q["instructions"].replace("{IMG}", img)}
        if "criteria" in q:
            item["criteria"] = q["criteria"]
        questions[qid] = item
    return {"model": MODEL, "state": state, "questions": questions, "assets": assets}, sizes


def estimate_tokens(body, sizes):
    """Rough, for the dry run only: text at 4 characters per token; images at width x height / 750
    (an assumption; TypeSafe publishes no per-image figure)."""
    slim = json.dumps({k: v for k, v in body.items() if k != "assets"})
    return len(slim) // 4 + sum(w * h // 750 for w, h in sizes)


def get_key():
    k = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if not k and sys.stdin.isatty():
        k = getpass.getpass("Jev API key (typing is hidden): ").strip()
    if not k:
        raise SystemExit("No key: set JEV_API_KEY in this terminal first.")
    return k


def call(key, raw):
    req = urllib.request.Request(URL, data=raw, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "TypeSafe-Danger-Zone-Images": "true"})
    attempts = 0
    while True:
        attempts += 1
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read())
                return 200, body, round((time.perf_counter() - t0) * 1000), attempts
        except urllib.error.HTTPError as e:
            ms = round((time.perf_counter() - t0) * 1000)
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503, 529) and attempts < 5:
                time.sleep(float(e.headers.get("retry-after") or 2 ** attempts))
                continue
            return e.code, {"error": detail}, ms, attempts
        except (urllib.error.URLError, TimeoutError) as e:
            if attempts < 5:
                time.sleep(2 ** attempts)
                continue
            return 0, {"error": str(e)}, None, attempts


def summarise(answers):
    out = {}
    for qid, a in answers.items():
        out[qid] = a.get("noul") if a.get("type") == "noul" else {
            "choice": a.get("choice"), "confidence": a.get("confidence"), "probabilities": a.get("probabilities")}
    return out


def main():
    dry, dev = "--dry-run" in sys.argv, "--dev" in sys.argv
    cap = float(arg("--cap", "5.00"))
    prefix = "dev-" if dev else ""
    stems = [s for s in DEV_IMAGES if s in manifest()["images"]] if dev else sorted(manifest()["images"])
    plan = [(s, a) for a in ARMS for s in stems]
    if dry:
        est, biggest = 0, 0
        for s, a in plan:
            body, sizes = build(s, a)
            raw = json.dumps(body).encode()
            biggest = max(biggest, len(raw))
            est += estimate_tokens(body, sizes)
        print(f"{len(plan)} calls planned; {len(Q['defect_questions'])} defect questions per call in whole/tiles")
        print(f"largest request body: {biggest/1e6:.2f} MB (limit 32 MiB)")
        print(f"estimated input tokens: {est:,}; estimated cost: ${est*PRICE_PER_M_INPUT/1e6:.4f} (rough; see estimate_tokens)")
        return
    key = get_key()
    out_dir = DATA / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{prefix}outputs.jsonl"
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    spent, rows = 0.0, []
    with out_path.open("a") as f:
        for s, a in plan:
            body, _ = build(s, a)
            raw = json.dumps(body).encode()
            same_arm = [r["usage"].get("input_tokens", 0) for r in rows if r["status"] == 200 and r["arm"] == a]
            next_cost = (max(same_arm) if same_arm else 0) * PRICE_PER_M_INPUT / 1e6
            if spent + next_cost > cap:
                print(f"Stopping: the next call could take spend past the ${cap:.2f} cap (spent ${spent:.4f}).")
                break
            status, resp, ms, attempts = call(key, raw)
            usage = resp.get("usage", {}) if status == 200 else {}
            spent += usage.get("input_tokens", 0) * PRICE_PER_M_INPUT / 1e6
            row = {"run_id": run_id, "image": s, "arm": a, "model_requested": MODEL,
                   "model_answered": resp.get("model"), "status": status, "latency_ms": ms, "attempts": attempts,
                   "usage": usage, "answers": summarise(resp.get("answers", {})) if status == 200 else None,
                   "error": resp.get("error"), "request_sha256": sha(raw), "request_bytes": len(raw),
                   "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
            f.write(json.dumps(row) + "\n")
            f.flush()
            rows.append(row)
            print(f"{s} {a:5} HTTP {status} {ms} ms in={usage.get('input_tokens')} spent=${spent:.4f}")
            if status in (400, 401, 403, 422):
                print("Stopping on a request error; nothing else will be sent. Error:", (resp.get("error") or "")[:300])
                break
    ok = [r for r in rows if r["status"] == 200]
    log = {"run_id": run_id, "set": "dev" if dev else "full", "model_requested": MODEL,
           "models_answered": sorted({r["model_answered"] for r in ok if r["model_answered"]}),
           "calls": len(rows), "ok": len(ok), "errors": [[r["image"], r["arm"], r["status"]] for r in rows if r["status"] != 200],
           "input_tokens": sum(r["usage"].get("input_tokens", 0) for r in ok),
           "output_tokens": sum(r["usage"].get("output_tokens", 0) for r in ok),
           "price_per_m_input_usd": PRICE_PER_M_INPUT, "cap_usd": cap}
    log["cost_usd"] = round(log["input_tokens"] * PRICE_PER_M_INPUT / 1e6, 6)
    for a in sorted({r["arm"] for r in ok}):
        lat = [r["latency_ms"] for r in ok if r["arm"] == a]
        tok = [r["usage"].get("input_tokens", 0) for r in ok if r["arm"] == a]
        log[f"{a}_median_latency_ms"] = statistics.median(lat)
        log[f"{a}_mean_input_tokens"] = round(statistics.mean(tok))
    (out_dir / f"{prefix}run-log.json").write_text(json.dumps(log, indent=1))
    print(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
