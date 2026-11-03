# jev-image-checks

Can a cheap yes/no model check AI-generated product images? This repo holds the code and the saved results of a field test by the team at LARGE FORMAT: Jev 1.14, TypeSafe AI's typed decision model, asked 129 yes/no questions about each of 29 AI-generated product images of an electric bike, and scored against a full Claude vision audit.

Write-up: https://www.lrgfrmt.com/lab/jev-image-checks

- Jev is advice only. In the workflow this test was built for, a person approves every image.
- The product images themselves are not published. The saved answers, labels and scoring are, so every figure can be recomputed.
- Image reading is an experimental Jev feature. TypeSafe says it is not yet ready for production.

## What is here

| Path | What it is |
|---|---|
| `questions.json` | The 129 yes/no questions (52 core, 77 detail), grouped under 43 checklist items, plus the camera-angle question and the shared context sent with every image |
| `prepare.py` | Resizes your images to 1,568 px on the long side (the API's limit), cuts four close-up quarters, and writes a manifest with a fingerprint of every file |
| `run_jev.py` | Calls Jev once per image for each of three ways of asking (whole image, with close-ups, camera angle). Standard library only |
| `score.py` | Scores the saved answers against the labels and writes `metrics.json` and `metrics.md` |
| `data/labels.json` | The labels: one verdict per image and checklist item from the audit (problem, fine or unsure), the serious problems, and each image's camera angle |
| `data/runs/` | The saved Jev answers from the test (`outputs.jsonl`, 87 calls; `dev-outputs.jsonl`, 9 wiring calls), the run logs and the metrics |
| `data/inputs/manifest.json` | The sizes and fingerprints of the resized images that were sent |

In this public copy, `BRAND` and `MAKER` in `questions.json` stand in for the real battery wordmark and the gear maker's name used in the test.

## Recompute the results

```bash
python3 score.py
```

No key and no network. It reads `data/runs/outputs.jsonl` and `data/labels.json` and prints the same figures as `data/runs/metrics.md`.

## Run it on your own images

```bash
pip install pillow
python3 prepare.py path/to/images      # names like A-01-right-side.jpg: an id, then the intended view
read -s JEV_API_KEY && export JEV_API_KEY
python3 run_jev.py --dry-run           # no network: builds every request, estimates cost
python3 run_jev.py --dev               # 3 images, 9 calls, checks the wiring
python3 run_jev.py                     # every image, three ways of asking
python3 score.py
```

You need a TypeSafe API key and your own labels in `data/labels.json`, in the same shape as the file here: `items` maps each image id to `{checklist item: "problem" | "fine" | "unsure"}`, `serious` lists the serious items per image, and `views` gives each image's actual camera angle as `label`. The key is read from your environment and never printed or saved. The script stops before any call that could take spend past a cap (default $5).

## How scoring works

- An item's score is the highest yes-probability among its questions. Every question is worded so that yes means a problem is visible.
- The gate passes an item when that score is 0.3 or lower, the starting point TypeSafe's docs suggest for "no". 0.5 and 0.7 are reported too.
- A false pass is a check the audit failed that the gate passed. A false alarm is a check the audit found fine that the gate flagged.
- Held out means every image except the 3 used to check the wiring (A-01, B-05, C-10).

## What we found

At the suggested setting, the 52 core questions caught 80 of 91 known problems on the 26 held-out images, but let 11 through, one of them serious, and sent 48% of checks on to the audit. Two core kickstand questions together caught 12 of 12 kickstands left down, with 1 false alarm in 16. One whole image with 129 questions cost about $0.51 per 1,000 images at $42 per billion input tokens (TypeSafe's published price on 3 November 2026), with a median of 290 ms. The full write-up has the limits: the labels are a Claude audit, not a person; 29 images, one run; and the detail questions were written after reading the audit's findings.

## Licence

MIT. See `LICENSE`.
