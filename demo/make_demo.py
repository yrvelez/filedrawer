"""Generate the SYNTHETIC demo survey export (Qualtrics-style CSV with 3 header rows).

Nothing here is real data. The file includes fake identifier columns so the PII
stage has something to strip, and a canary string in a free-text column so tests
can prove no respondent text ever reaches an LLM request.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

CANARY = "CANARY-7f3a9e"
SEED = 2024
N = 400

SYSTEM_COLS = ["StartDate", "EndDate", "Status", "IPAddress", "Progress", "Duration (in seconds)",
               "Finished", "RecordedDate", "ResponseId", "RecipientLastName", "RecipientFirstName",
               "RecipientEmail", "ExternalReference", "LocationLatitude", "LocationLongitude",
               "DistributionChannel", "UserLanguage"]
Q_COLS = ["consent", "pid3", "ideo5", "agecat", "gender", "educ", "imm_att_1", "imm_att_2", "imm_att_3",
          "policy_support", "attn", "open_comment", "condition"]
Q_TEXT = {
    "consent": "Do you consent to participate in this study?",
    "pid3": "Generally speaking, do you usually think of yourself as a Democrat, a Republican, an Independent, or something else?",
    "ideo5": "In general, how would you describe your political views?",
    "agecat": "What is your age?", "gender": "How do you describe yourself?",
    "educ": "What is the highest level of education you have completed?",
    "imm_att_1": "How much do you agree or disagree with the following statements? - Immigrants strengthen the U.S. economy.",
    "imm_att_2": "How much do you agree or disagree with the following statements? - Immigration levels should be reduced.",
    "imm_att_3": "How much do you agree or disagree with the following statements? - Undocumented immigrants should have a path to citizenship.",
    "policy_support": "Overall, do you favor or oppose increasing the number of legal immigrants admitted to the United States each year?",
    "attn": "To check that you are reading carefully, please select 'Somewhat agree' below.",
    "open_comment": "Is there anything else you would like to tell us about your views on immigration? (Optional)",
    "condition": "condition",
}
IMPORT_IDS = {"consent": "QID1", "pid3": "QID2", "ideo5": "QID3", "agecat": "QID4", "gender": "QID5",
              "educ": "QID6", "imm_att_1": "QID9_1", "imm_att_2": "QID9_2", "imm_att_3": "QID9_3",
              "policy_support": "QID12", "attn": "QID10", "open_comment": "QID11_TEXT", "condition": "condition"}

COMMENTS = ["", "", "", "", "", "No.", "Immigration is complicated.", "We need better border security.",
            "My grandparents were immigrants.", "n/a", "Nothing else.", "Legal immigration is fine."]


def _clip7(x: float) -> int:
    return int(max(1, min(7, round(x))))


def generate(path: str | Path, n: int = N, seed: int = SEED) -> Path:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        pid = rng.choices([1, 2, 3, 4], weights=[0.40, 0.35, 0.20, 0.05])[0]
        ideo = {1: [1, 2, 2, 3], 2: [3, 4, 4, 5], 3: [2, 3, 3, 4], 4: [1, 3, 3, 5]}[pid][rng.randrange(4)]
        agecat = rng.choices([1, 2, 3, 4], weights=[0.22, 0.28, 0.32, 0.18])[0]
        gender = rng.choices([1, 2, 3], weights=[0.48, 0.49, 0.03])[0]
        educ = rng.choices([1, 2, 3, 4], weights=[0.30, 0.28, 0.27, 0.15])[0]
        cond = "treatment" if rng.random() < 0.5 else "control"
        base = {1: 5.2, 2: 3.4, 3: 4.3, 4: 4.3}[pid] + rng.gauss(0, 0.9)
        effect = {1: 0.7, 2: 0.3, 3: 0.5, 4: 0.5}[pid] if cond == "treatment" else 0.0
        latent = base + effect
        a1 = _clip7(latent + rng.gauss(0, 0.8))
        a2 = _clip7(8 - latent + rng.gauss(0, 0.8))          # reverse-worded
        a3 = _clip7(latent - 0.3 + rng.gauss(0, 0.9))
        pol = _clip7(latent - 0.5 + (0.4 if cond == "treatment" else 0) + rng.gauss(0, 1.0))
        attn = 5 if rng.random() < 0.86 else rng.randrange(1, 8)
        comment = rng.choice(COMMENTS)
        if i == 0:
            comment = f"{CANARY} this respondent text must never leave the machine"
        finished = 1 if rng.random() < 0.97 else 0
        rows.append({
            "StartDate": f"2024-03-{(i % 28) + 1:02d} 10:{i % 60:02d}:00", "EndDate": f"2024-03-{(i % 28) + 1:02d} 10:{(i + 7) % 60:02d}:00",
            "Status": "IP Address", "IPAddress": f"203.0.113.{i % 255}", "Progress": 100 if finished else 60,
            "Duration (in seconds)": rng.randrange(180, 900), "Finished": finished,
            "RecordedDate": f"2024-03-{(i % 28) + 1:02d} 10:{(i + 8) % 60:02d}:00", "ResponseId": f"R_{rng.randrange(10**9):09d}",
            "RecipientLastName": "", "RecipientFirstName": "",
            "RecipientEmail": "canary.person@example.com" if i == 0 else "",
            "ExternalReference": f"EXT{i:05d}", "LocationLatitude": f"{40 + rng.random():.4f}", "LocationLongitude": f"{-74 - rng.random():.4f}",
            "DistributionChannel": "anonymous", "UserLanguage": "EN",
            "consent": 1, "pid3": pid, "ideo5": ideo, "agecat": agecat, "gender": gender, "educ": educ,
            "imm_att_1": a1, "imm_att_2": a2, "imm_att_3": a3, "policy_support": pol, "attn": attn,
            "open_comment": comment, "condition": cond,
        })
    cols = SYSTEM_COLS + Q_COLS
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerow([Q_TEXT.get(c, c) for c in cols])
        w.writerow([json.dumps({"ImportId": IMPORT_IDS.get(c, c.replace(" ", "_").lower())}) for c in cols])
        for r in rows:
            w.writerow([r[c] for c in cols])
    return path


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "demo/demo_raw.csv"
    print(generate(out))
