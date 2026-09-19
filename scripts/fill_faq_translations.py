import json, time, re, sys
sys.path.insert(0, ".")
from core.gemini_tutor_service import ask_tutor_gemini
from scripts.harvest_faq_from_gemini import FAQ_PATH, _save_faq

with open(FAQ_PATH, encoding="utf-8") as f:
    data = json.load(f)
by_id = {e["id"]: e for e in data["entries"]}
need = [eid for eid, e in by_id.items() if not (e["responses"].get("mr") or "").strip() or not (e["responses"].get("hi") or "").strip()]
print("need", len(need))

for i, eid in enumerate(need, 1):
    entry = by_id[eid]
    en = (entry["responses"].get("en") or "").strip()
    if not en:
        continue
    prompt = (
        "Translate this pharmacy help answer to Marathi (mr) and Hindi (hi). "
        'Keep English UI labels. Reply ONLY as JSON: {"mr":"...","hi":"..."}\n\n' + en
    )
    print(f"[{i}/{len(need)}] {eid}")
    for attempt in range(5):
        try:
            raw = ask_tutor_gemini(prompt, context={"screen_id": "overview"})
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                raise ValueError("no json")
            obj = json.loads(m.group())
            if not (entry["responses"].get("mr") or "").strip() and obj.get("mr"):
                entry["responses"]["mr"] = str(obj["mr"]).strip()
            if not (entry["responses"].get("hi") or "").strip() and obj.get("hi"):
                entry["responses"]["hi"] = str(obj["hi"]).strip()
            break
        except Exception as exc:
            msg = str(exc)
            wait = 65 if "quota" in msg.lower() else 5
            print("  retry", attempt + 1, wait, msg[:100])
            time.sleep(wait)
    _save_faq(data, by_id)
    time.sleep(40)

missing = sum(1 for e in by_id.values() if not (e["responses"].get("mr") or "").strip() or not (e["responses"].get("hi") or "").strip())
print("remaining incomplete", missing)
