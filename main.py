"""
Pratta Thailand — Bitrix24 Automation Server
Полная автоматизация CRM: роботы, задачи, уведомления, дожим
"""

import os, json, time, threading, requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

WEBHOOK = os.environ.get("BITRIX_WEBHOOK", "https://pratta.bitrix24.ru/rest/1/28ffhnvxrpnengiw")
BOT_SECRET = os.environ.get("BOT_SECRET", "pratta2025")

USERS = {
    "rop":    int(os.environ.get("USER_ROP",    "1")),
    "buh":    int(os.environ.get("USER_BUH",    "1")),
    "sklad":  int(os.environ.get("USER_SKLAD",  "1")),
    "koler":  int(os.environ.get("USER_KOLER",  "1")),
    "logist": int(os.environ.get("USER_LOGIST", "1")),
}

PIPELINE_STAGES = {}

# ── API ──────────────────────────────────────────────────────────────────────

def b24(method, params=None):
    url = f"{WEBHOOK}/{method}.json"
    try:
        r = requests.post(url, json=params or {}, timeout=15)
        data = r.json()
        if "error" in data:
            print(f"[B24 ERROR] {method}: {data.get('error_description', data['error'])}")
        return data.get("result")
    except Exception as e:
        print(f"[B24 EXCEPTION] {method}: {e}")
        return None

def get_deal(deal_id):
    return b24("crm.deal.get", {"id": deal_id})

def update_deal(deal_id, fields):
    return b24("crm.deal.update", {"id": deal_id, "fields": fields})

def move_deal(deal_id, stage_id):
    return update_deal(deal_id, {"STAGE_ID": stage_id})

def create_task(title, responsible_id, deal_id, description="", deadline_hours=24):
    deadline = (datetime.now() + timedelta(hours=deadline_hours)).strftime("%Y-%m-%dT%H:%M:%S")
    return b24("tasks.task.add", {"fields": {
        "TITLE": title,
        "RESPONSIBLE_ID": responsible_id,
        "DESCRIPTION": description,
        "DEADLINE": deadline,
        "UF_CRM_TASK": [f"D_{deal_id}"],
    }})

def notify(user_id, message, deal_id=None):
    return b24("im.notify.personal.add", {
        "USER_ID": user_id,
        "MESSAGE": message,
        "TAG": f"DEAL_{deal_id}" if deal_id else "",
    })

def notify_many(user_ids, message, deal_id=None):
    for uid in user_ids:
        notify(uid, message, deal_id)

def get_contact(cid):
    return (b24("crm.contact.get", {"id": cid}) or {}) if cid else {}

def deal_info(deal):
    title = deal.get("TITLE", "—")
    c = get_contact(deal.get("CONTACT_ID"))
    name = f"{c.get('NAME','')} {c.get('LAST_NAME','')}".strip() or "—"
    return title, name

# ── ЗАГРУЗКА СТАДИЙ ──────────────────────────────────────────────────────────

def load_stages():
    global PIPELINE_STAGES
    PIPELINE_STAGES = {}
    stages = b24("crm.status.list", {"filter": {"ENTITY_ID": "DEAL_STAGE"}}) or []
    PIPELINE_STAGES["main"] = {s["NAME"]: s["STATUS_ID"] for s in stages}
    pipelines = b24("crm.dealcategory.list") or []
    for p in pipelines:
        cid = p["ID"]
        stages = b24("crm.status.list", {"filter": {"ENTITY_ID": f"DEAL_STAGE_{cid}"}}) or []
        PIPELINE_STAGES[p["NAME"]] = {s["NAME"]: s["STATUS_ID"] for s in stages}
    print(f"[STAGES] Загружено: {list(PIPELINE_STAGES.keys())}")

def get_stage_id(pipeline, stage_name):
    return PIPELINE_STAGES.get(pipeline, {}).get(stage_name)

def get_pipeline_name(deal):
    cat_id = str(deal.get("CATEGORY_ID", "0"))
    for p in (b24("crm.dealcategory.list") or []):
        if str(p["ID"]) == cat_id:
            return p["NAME"]
    return "main"

# ── ЛОГИКА АВТОМАТИЗАЦИИ ─────────────────────────────────────────────────────

def on_new_deal(deal):
    did = deal["ID"]
    title, contact = deal_info(deal)
    resp = int(deal.get("ASSIGNED_BY_ID", USERS["rop"]))

    create_task(f"Связаться с клиентом — {title}", resp, did,
        f"Новый лид! Клиент: {contact}\nСвяжись в течение 15 минут.", 0.25)
    notify(resp, f"Новый лид!\nКлиент: {contact}\nСделка: {title}\nСвяжись в течение 15 минут!", did)

    def escalate():
        time.sleep(900)
        d = get_deal(did)
        if d and d.get("STAGE_ID") == deal.get("STAGE_ID"):
            notify(USERS["rop"], f"Лид без ответа 15 минут!\nКлиент: {contact}\nСделка: {title}", did)
    threading.Thread(target=escalate, daemon=True).start()
    print(f"[NEW DEAL] {title}")

def on_stage_change(deal, old_stage, new_stage):
    did = deal["ID"]
    title, contact = deal_info(deal)
    resp = int(deal.get("ASSIGNED_BY_ID", USERS["rop"]))

    print(f"[STAGE] {title}: {old_stage} -> {new_stage}")

    # БОТ СТАДИИ
    if new_stage in ["1st message no answer", "2nd message no answer", "3rd message no answer"]:
        def bot_esc(s=new_stage, sid=deal.get("STAGE_ID")):
            time.sleep(86400)
            d = get_deal(did)
            if d and d.get("STAGE_ID") == sid:
                notify(resp, f"Клиент не отвечает ({s})\nКлиент: {contact}\nПопробуй другой канал.", did)
        threading.Thread(target=bot_esc, daemon=True).start()

    elif new_stage == "Квалификация":
        create_task(f"Квалификация клиента — {title}", resp, did,
            f"Клиент: {contact}\nТип клиента, объект, объём м², что красим, стадия проекта.", 24)
        def check():
            time.sleep(86400)
            d = get_deal(did)
            if d and d.get("STAGE_ID") == deal.get("STAGE_ID"):
                notify(USERS["rop"], f"Сделка зависла на квалификации >1 дня\nКлиент: {contact}", did)
        threading.Thread(target=check, daemon=True).start()

    elif new_stage == "Согласование":
        create_task(f"Согласовать детали — {title}", resp, did,
            f"Клиент: {contact}\nВнутри/снаружи, мокрые зоны, объём, цвет, бюджет.", 24)

    elif new_stage == "Коммерческое предложение":
        create_task(f"Подготовить КП — {title}", resp, did,
            f"Клиент: {contact}\nОбъём + 10%. До 5000м² retail. От 5000м² developer price.\nОтправить: вёдра + цена + пояснение логики.", 4)
        notify(resp, f"КП нужно отправить в течение 4 часов\nКлиент: {contact}", did)

        def kp_check(sid=deal.get("STAGE_ID")):
            time.sleep(14400)
            d = get_deal(did)
            if d and d.get("STAGE_ID") == sid:
                notify(resp, f"КП не отправлено 4 часа!\nКлиент: {contact}", did)
                time.sleep(14400)
                d = get_deal(did)
                if d and d.get("STAGE_ID") == sid:
                    notify(USERS["rop"], f"КП не отправлено 8 часов!\nСделка: {title}", did)
        threading.Thread(target=kp_check, daemon=True).start()

    elif new_stage == "Согласование и дожим":
        create_task(f"Получить ответ по КП — {title}", resp, did,
            f"Клиент: {contact}\nОтработать возражения. Follow-up.", 72)

        def dojim(sid=deal.get("STAGE_ID")):
            time.sleep(86400)
            d = get_deal(did)
            if d and d.get("STAGE_ID") == sid:
                notify(resp, f"Клиент не ответил на КП 1 день\nКлиент: {contact}", did)
                time.sleep(172800)
                d = get_deal(did)
                if d and d.get("STAGE_ID") == sid:
                    notify(resp, f"Follow-up: 3 дня без ответа\nКлиент: {contact}", did)
        threading.Thread(target=dojim, daemon=True).start()

    elif new_stage in ["Счёт и договор", "Инвойс"]:
        create_task(f"Выставить Invoice — {title}", USERS["buh"], did,
            f"Клиент: {contact}\nВёдра + колеровка + стоимость + доставка отдельно.", 2)
        notify(USERS["buh"], f"Нужно выставить Invoice\nСделка: {title}\nКлиент: {contact}", did)

        def inv_check(sid=deal.get("STAGE_ID")):
            time.sleep(7200)
            d = get_deal(did)
            if d and d.get("STAGE_ID") == sid:
                notify(resp, f"Invoice не выставлен 2 часа!\nКлиент: {contact}", did)
        threading.Thread(target=inv_check, daemon=True).start()

    elif new_stage == "Оплата":
        create_task(f"Проконтролировать оплату — {title}", USERS["buh"], did,
            f"Клиент: {contact}\nПри оплате — выдать Receipt, перевести в Колеровку.", 48)

        def pay_check(sid=deal.get("STAGE_ID")):
            time.sleep(82800)
            d = get_deal(did)
            if d and d.get("STAGE_ID") == sid:
                notify(resp, f"Напомни клиенту об оплате\nКлиент: {contact}", did)
                time.sleep(3600)
                d = get_deal(did)
                if d and d.get("STAGE_ID") == sid:
                    notify_many([resp, USERS["buh"]],
                        f"ПРОСРОЧКА ОПЛАТЫ!\nСделка: {title}\nКлиент: {contact}", did)
        threading.Thread(target=pay_check, daemon=True).start()

    elif new_stage == "Колеровка":
        create_task(f"Списать товар → колеровка — {title}", USERS["sklad"], did,
            f"Клиент: {contact}\nСписать товар, передать колеровщику с кодом цвета.", 8)
        def koler_task():
            time.sleep(7200)
            create_task(f"Выполнить колеровку — {title}", USERS["koler"], did,
                f"Клиент: {contact}\nВыполнить по коду цвета, уведомить менеджера.", 8)
        threading.Thread(target=koler_task, daemon=True).start()

    elif new_stage in ["Отгрузка и логистика", "Реализация и отгрузка", "Реализация"]:
        create_task(f"Организовать отгрузку — {title}", USERS["logist"], did,
            f"Клиент: {contact}\nДоставка или самовывоз. Уведомить клиента о готовности.", 8)
        notify(resp, f"Товар готов к отгрузке!\nКлиент: {contact}", did)

    elif new_stage in ["Завершено", "Закрыто успешно"]:
        create_task(f"Запросить отзыв — {title}", resp, did,
            f"Клиент: {contact}\nОтзыв + фото через 2-3 дня.", 72)
        notify(resp, f"Сделка закрыта успешно!\nКлиент: {contact}\nЗапроси отзыв через 2-3 дня.", did)

    elif new_stage in ["Закрыто без продажи", "Закрыто отказ"]:
        create_task(f"Заполнить причину отказа — {title}", resp, did,
            f"ОБЯЗАТЕЛЬНО: заполнить поле «Причина отказа».\nКлиент: {contact}", 4)
        def retry():
            time.sleep(86400 * 30)
            create_task(f"Повторный контакт — {title}", resp, did,
                f"30 дней прошло. Возможно клиент готов.\nКлиент: {contact}", 48)
        threading.Thread(target=retry, daemon=True).start()

    elif new_stage == "Договор подписан":
        create_task(f"Подтвердить дату старта — {title}", resp, did,
            f"Клиент: {contact}\nПодтвердить дату работ, проверить документы.", 24)
        create_task(f"Проверить документы — {title}", USERS["buh"], did,
            f"Договор на нанесение. Клиент: {contact}", 24)

    elif new_stage == "Выход бригады":
        create_task(f"Выход бригады — {title}", resp, did,
            f"Клиент: {contact}\nМастера, кол-во, дата старта. Объект и материал готовы?", 2)

    elif new_stage == "Сдача объекта":
        create_task(f"Подписать акт и фото — {title}", resp, did,
            f"Клиент: {contact}\nПодписать акт. Финальные фото. Запросить отзыв.", 24)

    elif new_stage == "Обучение":
        create_task(f"Провести обучение дилера — {title}", resp, did,
            f"Дилер: {contact}\nНазначить дату и провести обучение.", 72)

# ── ВЕБХУКИ ──────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.form.to_dict()
    event = data.get("event", "")
    print(f"[WEBHOOK] {event}")

    if event == "ONCRMDEALADD":
        did = data.get("data[FIELDS][ID]")
        if did:
            deal = get_deal(did)
            if deal:
                threading.Thread(target=on_new_deal, args=(deal,), daemon=True).start()

    elif event == "ONCRMDEALUPDATE":
        did = data.get("data[FIELDS][ID]")
        if did:
            deal = get_deal(did)
            if deal:
                old = data.get("data[PREVIOUS][STAGE_ID]", "")
                new = deal.get("STAGE_ID", "")
                if old != new:
                    threading.Thread(target=on_stage_change, args=(deal, old, new), daemon=True).start()

    return jsonify({"status": "ok"})

@app.route("/move", methods=["POST"])
def move():
    if request.headers.get("X-Bot-Secret") != BOT_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    body = request.json or {}
    did = body.get("deal_id")
    stage_name = body.get("stage")
    pipeline = body.get("pipeline", "Paint — Продажа краски")
    if not did or not stage_name:
        return jsonify({"error": "deal_id and stage required"}), 400
    sid = get_stage_id(pipeline, stage_name)
    if not sid:
        return jsonify({"error": f"Stage '{stage_name}' not found"}), 404
    result = move_deal(did, sid)
    return jsonify({"status": "ok", "stage_id": sid, "result": result})

@app.route("/stages", methods=["GET"])
def stages():
    return jsonify(PIPELINE_STAGES)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/setup", methods=["POST"])
def setup():
    url = (request.json or {}).get("server_url", "")
    if not url:
        return jsonify({"error": "server_url required"}), 400
    load_stages()
    for event in ["ONCRMDEALADD", "ONCRMDEALUPDATE"]:
        b24("event.bind", {"event": event, "handler": f"{url}/webhook", "auth_type": 0})
    return jsonify({"status": "ok", "stages": PIPELINE_STAGES})

@app.route("/", methods=["GET"])
def index():
    return jsonify({"name": "Pratta Automation", "status": "running"})

if __name__ == "__main__":
    print("Pratta Thailand — Bitrix24 Automation Server")
    load_stages()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
