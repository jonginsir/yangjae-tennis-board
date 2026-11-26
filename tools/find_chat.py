"""알림을 보낼 그룹방의 '현재' ID를 찾아서 그 그룹방에 올린다.
- Secrets 값(앞 '-' 빠짐, 슈퍼그룹 전환 전 옛 ID 등)과 봇이 받은 메시지에서 후보를 모은다.
- getChat으로 확인하고, 슈퍼그룹으로 바뀌었으면 텔레그램이 알려주는 새 ID(-100...)를 따라간다.
ID는 공개 로그에 남기지 않는다."""
import os
import sys

import requests

sys.path.insert(0, os.getcwd())
from collector.run import chat_ids  # noqa: E402

tok = os.environ["TELEGRAM_TOKEN"]
api = f"https://api.telegram.org/bot{tok}"

cands = []
for c in chat_ids(os.environ.get("TELEGRAM_CHAT_ID", "")):
    cands += [c] if c.startswith("-") else [c, "-" + c]
for u in requests.get(f"{api}/getUpdates", timeout=20).json().get("result", []):
    for key in ("message", "my_chat_member", "edited_message"):
        msg = u.get(key) or {}
        chat = msg.get("chat") or {}
        if chat.get("type") in ("group", "supergroup"):
            cands.append(str(chat["id"]))
        if msg.get("migrate_to_chat_id"):
            cands.append(str(msg["migrate_to_chat_id"]))


def resolve(cid, depth=0):
    r = requests.get(f"{api}/getChat", params={"chat_id": cid}, timeout=20).json()
    if r.get("ok"):
        return str(r["result"]["id"]), r["result"].get("title", "")
    new = (r.get("parameters") or {}).get("migrate_to_chat_id")
    return resolve(str(new), depth + 1) if new and depth < 2 else (None, None)


found = {}
for c in dict.fromkeys(cands):
    rid, title = resolve(c)
    if rid and rid.startswith("-"):
        found[rid] = title
print("candidates:", len(cands), "groups resolved:", len(found))
for rid, title in found.items():
    text = (f"🔧 이 그룹방의 현재 ID예요. (그룹명: {title})\n\n{rid}\n\n"
            "GitHub Secrets의 TELEGRAM_CHAT_ID를 위 숫자로 바꿔주세요. 맨 앞 '-'까지 꼭 같이 복사해요.")
    print("send:", requests.post(f"{api}/sendMessage", json={"chat_id": rid, "text": text}, timeout=20).status_code)
