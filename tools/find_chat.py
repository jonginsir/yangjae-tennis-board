"""봇이 들어가 있는 그룹방의 ID를 찾아서 형 개인 채팅(TELEGRAM_CHAT_ID)으로 보낸다.
ID는 공개 로그에 남기지 않는다(로그에는 개수만)."""
import os
import requests

tok, me = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
api = f"https://api.telegram.org/bot{tok}"
r = requests.get(f"{api}/getUpdates", params={"allowed_updates": '["message","my_chat_member"]'}, timeout=20).json()
groups = {}
migrated = {}
for u in r.get("result", []):
    for key in ("message", "my_chat_member", "edited_message"):
        msg = u.get(key) or {}
        chat = msg.get("chat")
        if chat and chat.get("type") in ("group", "supergroup"):
            groups[chat["id"]] = chat.get("title", "(이름 없음)")
        if msg.get("migrate_to_chat_id"):  # 일반 그룹 → 슈퍼그룹으로 바뀌면 ID가 -100... 으로 바뀐다
            migrated[chat["id"]] = msg["migrate_to_chat_id"]
for old, new in migrated.items():
    groups[new] = groups.pop(old, groups.get(new, "(이름 없음)"))
print("getUpdates ok:", r.get("ok"), "groups found:", len(groups), r.get("description", ""))
if groups:
    lines = [f"{title}\n{cid}" for cid, title in groups.items()]
    text = ("👥 봇이 들어가 있는 그룹방을 찾았어요.\n아래 숫자(마이너스 포함)를 GitHub Secrets의 TELEGRAM_CHAT_ID에 넣으면 돼요.\n\n"
            + "\n\n".join(lines))
else:
    text = ("👥 아직 그룹방을 못 찾았어요.\n그룹방에 봇을 추가한 다음, 그룹방에서 /start@봇아이디 를 한 번 보내고 다시 실행해 주세요.\n"
            "(봇이 받은 지 24시간이 지난 메시지는 안 보여요)")
res = requests.post(f"{api}/sendMessage", json={"chat_id": me, "text": text}, timeout=20)
print("send to secret chat:", res.status_code)
if res.status_code != 200:  # Secrets 값이 틀렸으면 그룹방에 직접 올린다
    for cid in groups:
        rr = requests.post(f"{api}/sendMessage", json={"chat_id": cid, "text": text}, timeout=20)
        print("send to group:", rr.status_code)
