"""List cards you flagged as wrong in the app (Firestore `cards`, field reported == true). READ-ONLY.

    python reported_cards.py            # print them
    python reported_cards.py --json     # machine-readable (for fixing them in bulk)

Fields written by the app: reported (bool), reportedAt (timestamp).
"""
import argparse
import json
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

SERVICE_ACCOUNT_PATH = Path(__file__).resolve().parent / "serviceAccountKey.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    firebase_admin.initialize_app(credentials.Certificate(str(SERVICE_ACCOUNT_PATH)))
    db = firestore.client()
    rows = []
    for snap in db.collection("cards").where(filter=FieldFilter("reported", "==", True)).stream():
        d = snap.to_dict()
        rows.append({"id": snap.id, "topic": d.get("topic"), "front": d.get("front"), "back": d.get("back"),
                     "source": d.get("source"), "sourceTitle": d.get("sourceTitle"), "evidence": d.get("evidence"),
                     "reportedAt": str(d.get("reportedAt"))})
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(f"hedef proje: {db.project} | bildirilen kart: {len(rows)}")
    for r in rows:
        print(f"\n[{r['id']}] {r['topic']} — {r['sourceTitle']}\n  S: {r['front']}\n  C: {r['back']}\n  alıntı: {(r['evidence'] or '')[:160]}\n  kaynak: {r['source']}")


if __name__ == "__main__":
    main()
