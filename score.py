#!/usr/bin/env python3
"""Score the Jev run against the labels. No network. Every figure in the article comes from here.

Rules (rubric.md):
- A checklist item's Jev score = the highest yes-probability among its questions in the question set
  being scored: "core" (the 52 questions written first) or "full" (core plus detail).
- The triage gate passes an item when that score is at or below the threshold (default 0.3, the docs'
  suggested "confident no"). Anything above goes on to the Claude audit and a person.
- False pass = the label says there is a problem and the gate passed it (the costly error).
- Labels: problem / fine; "unsure" labels are not scored.
- Held out = every generated image except the 3 dev images used to check the wiring.
- View sorting is scored against each image's labelled camera angle.

Usage: python3 score.py [--outputs <file>] [--out <dir>]
Writes metrics.json and metrics.md.
"""
import json, os, sys, pathlib, statistics
from collections import defaultdict, Counter

HERE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ.get("JEV_TEST_DATA") or HERE / "data")
PRICE_PER_M_INPUT = 0.042
DEV_IMAGES = {"A-01", "B-05", "C-10"}
THRESHOLDS = [0.3, 0.5, 0.7]


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))] if xs else None


def load(path):
    rows = [json.loads(l) for l in pathlib.Path(path).read_text().splitlines() if l.strip()]
    latest = {}
    for r in rows:                                   # keep the last successful answer per image and arm
        if r["status"] == 200:
            latest[(r["image"], r["arm"])] = r
    return latest


def cost_block(recs):
    lat = [r["latency_ms"] for r in recs]
    tok = [r["usage"].get("input_tokens", 0) for r in recs]
    return {"calls": len(recs), "median_latency_ms": statistics.median(lat), "p90_latency_ms": p90(lat),
            "input_tokens": sum(tok), "mean_input_tokens_per_call": round(statistics.mean(tok)),
            "cost_usd": round(sum(tok) * PRICE_PER_M_INPUT / 1e6, 6),
            "cost_per_1000_images_usd": round(statistics.mean(tok) * 1000 * PRICE_PER_M_INPUT / 1e6, 4)}


def score_defects(recs, q_items, L, imgs, t):
    per_item, tot = {}, Counter()
    img_flag, img_problem, serious_missed = {}, {}, []
    for img in imgs:
        ans = recs[img]["answers"]
        img_flag[img] = img_problem[img] = False
        for it, qids in q_items.items():
            score = max(ans[q] for q in qids)
            flagged = score > t
            img_flag[img] |= flagged
            lab = L["items"][img].get(it)
            if lab not in ("problem", "fine"):
                continue
            prob = lab == "problem"
            img_problem[img] |= prob
            d = per_item.setdefault(it, Counter())
            d["n"] += 1; d["problems"] += prob; d["flagged"] += flagged
            d["caught"] += prob and flagged; d["false_pass"] += prob and not flagged
            d["false_alarm"] += (not prob) and flagged
            if prob and not flagged and it in L["serious"].get(img, []):
                serious_missed.append([img, it, round(score, 3)])
    for d in per_item.values():
        tot.update(d)
    per_item = {k: dict(v, agreement=round((v["n"] - v["false_pass"] - v["false_alarm"]) / v["n"], 3))
                for k, v in sorted(per_item.items(), key=lambda kv: (kv[0][0], int(kv[0][1:])))}
    serious_pairs = sum(len([x for x in L["serious"].get(i, []) if x in q_items]) for i in imgs)
    return {"item_checks_scored": tot["n"], "problems": tot["problems"], "caught": tot["caught"],
            "false_passes": tot["false_pass"], "false_alarms": tot["false_alarm"],
            "share_sent_on": round(tot["flagged"] / tot["n"], 3),
            "problem_catch_rate": round(tot["caught"] / tot["problems"], 3) if tot["problems"] else None,
            "agreement": round((tot["n"] - tot["false_pass"] - tot["false_alarm"]) / tot["n"], 3),
            "serious_pairs": serious_pairs, "serious_missed": serious_missed,
            "images_flagged": sum(img_flag.values()), "images_with_problem": sum(img_problem.values()),
            "images_with_problem_passed": sum(1 for i in imgs if img_problem[i] and not img_flag[i]),
            "images_clean_and_passed": sum(1 for i in imgs if not img_problem[i] and not img_flag[i]),
            "per_item": per_item}


def score_views(recs, truth):
    imgs = sorted(recs)
    pairs = [(i, truth[i], recs[i]["answers"]["view"]["choice"], recs[i]["answers"]["view"]["confidence"]) for i in imgs]
    wrong = [list(p) for p in pairs if p[1] != p[2]]
    per_class = defaultdict(Counter)
    for _, lab, got, _ in pairs:
        per_class[lab]["n"] += 1
        per_class[lab]["correct"] += lab == got
    is_view = lambda v: v != "detail"
    split_ok = sum(is_view(lab) == is_view(got) for _, lab, got, _ in pairs)
    return {"n": len(pairs), "correct": len(pairs) - len(wrong), "accuracy": round((len(pairs) - len(wrong)) / len(pairs), 3),
            "always_detail_baseline": round(sum(lab == "detail" for _, lab, _, _ in pairs) / len(pairs), 3),
            "view_vs_detail_correct": split_ok,
            "per_class": {k: dict(v) for k, v in sorted(per_class.items())}, "wrong": wrong}


def main():
    outputs = pathlib.Path(arg("--outputs", DATA / "runs" / "outputs.jsonl"))
    out_dir = pathlib.Path(arg("--out", outputs.parent))
    Q = json.loads((HERE / "questions.json").read_text())
    L = json.loads((DATA / "labels.json").read_text())
    M, md = {"question_set_version": Q["version"]}, ["# Jev reference-sheet test: metrics (generated by score.py; do not edit)", ""]
    sets = {}
    for name in ["core", "full"]:
        qi = defaultdict(list)
        for qid, q in Q["defect_questions"].items():
            if name == "full" or q["set"] == "core":
                qi[q["item"]].append(qid)
        sets[name] = dict(qi)
    if outputs.exists():
        latest = load(outputs)
        M["outputs"] = str(outputs)
        M["models_answered"] = sorted({r["model_answered"] for r in latest.values() if r.get("model_answered")})
        for arm in ["whole", "tiles"]:
            recs = {img: r for (img, a), r in latest.items() if a == arm}
            if not recs:
                continue
            A = cost_block(list(recs.values()))
            for qs, q_items in sets.items():
                for scope in ["all", "held_out"]:
                    imgs = sorted(i for i in recs if scope == "all" or i not in DEV_IMAGES)
                    A[f"{qs}_{scope}"] = {"n_images": len(imgs), "items_scored": len(q_items),
                                          **{str(t): score_defects(recs, q_items, L, imgs, t) for t in THRESHOLDS}}
            M[arm] = A
            md.append(f"## Arm: {arm}")
            md.append(f"- median latency {A['median_latency_ms']} ms, p90 {A['p90_latency_ms']} ms; "
                      f"input tokens {A['input_tokens']:,}; cost ${A['cost_usd']}; per 1,000 images ${A['cost_per_1000_images_usd']}")
            for qs in sets:
                for scope in ["all", "held_out"]:
                    for t in THRESHOLDS:
                        s = A[f"{qs}_{scope}"][str(t)]
                        md.append(f"- {qs} questions, {scope}, gate {t}: {s['caught']}/{s['problems']} problems caught, "
                                  f"{s['false_passes']} false passes, {s['false_alarms']} false alarms, "
                                  f"{s['share_sent_on']:.0%} of checks sent on, serious missed {len(s['serious_missed'])}/{s['serious_pairs']}, "
                                  f"clean images passed {s['images_clean_and_passed']}")
            md += ["", f"| Item | Checks | Problems | Caught | False passes | False alarms | Agreement |  ({arm}, full questions, all images, gate 0.3)",
                   "|---|---|---|---|---|---|---|"]
            for it, d in A["full_all"]["0.3"]["per_item"].items():
                md.append(f"| {it} | {d['n']} | {d['problems']} | {d['caught']} | {d['false_pass']} | {d['false_alarm']} | {d['agreement']} |")
            md.append("")
        vrecs = {img: r for (img, a), r in latest.items() if a == "view"}
        if vrecs:
            truth = {i: L["views"][i]["label"] for i in vrecs}
            V = {**cost_block(list(vrecs.values())), "all": score_views(vrecs, truth),
                 "held_out": score_views({i: r for i, r in vrecs.items() if i not in DEV_IMAGES}, truth)}
            M["view"] = V
            md.append(f"## View sorting, generated images: {V['all']['correct']}/{V['all']['n']} correct "
                      f"(held out {V['held_out']['correct']}/{V['held_out']['n']}); median {V['median_latency_ms']} ms")
            md += [f"- {w[0]}: label {w[1]}, Jev {w[2]} (confidence {w[3]})" for w in V["all"]["wrong"]] + [""]
    blocks = [v for k, v in M.items() if isinstance(v, dict) and "input_tokens" in v]
    M["total_input_tokens"] = sum(b["input_tokens"] for b in blocks)
    M["total_cost_usd"] = round(M["total_input_tokens"] * PRICE_PER_M_INPUT / 1e6, 6)
    md.append(f"Total: {M['total_input_tokens']:,} input tokens, ${M['total_cost_usd']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(M, indent=1))
    (out_dir / "metrics.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
