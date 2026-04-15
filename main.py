import os, json, time, threading, requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

WEBHOOK = os.environ.get("BITRIX_WEBHOOK", "https://pratta.bitrix24.ru/rest/1/zmtfora4qlue5s02")
BOT_SECRET = os.environ.get("BOT_SECRET", "pratta2025")

USERS = {
    "rop":    int(os.environ.get("USER_ROP",    "1")),
    "buh":    int(os.environ.get("USER_BUH",    "1")),
    "sklad":  int(os.environ.get("USER_SKLAD",  "1")),
    "koler":  int(os.environ.get("USER_KOLER",  "1")),
    "logist": int(os.environ.get("USER_LOGIST", "1")),
}

PIPELINE_STAGES = {}

def b24(method, params=None):
    url = f"{WEBHOOK}/{method}.json"
    try:
        r = requests.post(url, json=params or {}, timeout=15)
        data = r.json()
        if "error" in data:
            print(f"[B24 ERROR] {method}: {data.get('error_description', data['error'])}")
            return None
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
    rop_id = USERS["rop"]
    # Наблюдатели: РОП всегда видит все задачи
    auditors = [rop_id] if int(responsible_id) != int(rop_id) else []
    result = b24("tasks.task.add", {"fields": {
        "TITLE": title,
        "RESPONSIBLE_ID": responsible_id,
        "DESCRIPTION": description,
        "DEADLINE": deadline,
        "UF_CRM_TASK": [f"D_{deal_id}"],
        "AUDITORS": auditors,
    }})
    if result:
        print(f"[TASK CREATED] {title} | resp={responsible_id} | auditors={auditors}")
    return result

def notify(user_id, message, deal_id=None):
    return b24("im.notify.personal.add", {
        "USER_ID": user_id,
        "MESSAGE": message,
        "TAG": f"DEAL_{deal_id}" if deal_id else "",
    })

def notify_many(user_ids, message, deal_id=None):
    for uid in user_ids:
        notify(uid, message, deal_id)

def deal_info(deal):
    title = deal.get("TITLE", "—")
    cid = deal.get("CONTACT_ID")
    name = "—"
    if cid and str(cid) not in ("0", "", "None"):
        try:
            c = b24("crm.contact.get", {"id": cid}) or {}
            name = f"{c.get('NAME','')} {c.get('LAST_NAME','')}".strip() or "—"
        except:
            pass
    return title, name

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
    print(f"[STAGES] {list(PIPELINE_STAGES.keys())}")

def get_stage_name(stage_id):
    """Возвращает название стадии по её ID"""
    for pipeline, stages in PIPELINE_STAGES.items():
        for name, sid in stages.items():
            if sid == stage_id:
                return name
    # Если не нашли - возвращаем ID
    return stage_id

def get_pipeline_by_stage_id(stage_id):
    """Возвращает название воронки по ID стадии"""
    for pipeline, stages in PIPELINE_STAGES.items():
        for name, sid in stages.items():
            if sid == stage_id:
                return pipeline
    return "main" 

def get_stage_id(pipeline, stage_name):
    if pipeline:
        sid = PIPELINE_STAGES.get(pipeline, {}).get(stage_name)
        if sid:
            return sid
    # Ищем во всех воронках
    for pip_name, stages in PIPELINE_STAGES.items():
        if stage_name in stages:
            return stages[stage_name]
    return None

def create_nanesenie_deal(source_deal):
    """Создаёт сделку в воронке Нанесение на основе сделки продажи"""
    try:
        source_id = source_deal["ID"]
        title = source_deal.get("TITLE", "—")
        contact_id = source_deal.get("CONTACT_ID")
        company_id = source_deal.get("COMPANY_ID")
        resp = source_deal.get("ASSIGNED_BY_ID", USERS["rop"])

        # Находим ID воронки Нанесение
        nanesenie_cat_id = None
        pipelines = b24("crm.dealcategory.list") or []
        for p in pipelines:
            if "Нанесение" in p["NAME"]:
                nanesenie_cat_id = p["ID"]
                break

        if not nanesenie_cat_id:
            print(f"[NANESENIE] Воронка Нанесение не найдена!")
            return

        # Находим первую стадию воронки Нанесение
        stages = b24("crm.status.list", {"filter": {"ENTITY_ID": f"DEAL_STAGE_{nanesenie_cat_id}"}}) or []
        # Сортируем по SORT и берём первую
        stages_sorted = sorted(stages, key=lambda s: int(s.get("SORT", 0)))
        first_stage = stages_sorted[0]["STATUS_ID"] if stages_sorted else None

        if not first_stage:
            print(f"[NANESENIE] Стадии воронки Нанесение не найдены!")
            return

        # Копируем только поля объекта (адрес, площадь, материал, цвет)
        source_fields = {}
        field_keys = [
            "UF_CRM_OBJECT_NAME",     # Название объекта
            "UF_CRM_OBJECT_LOCATION", # Адрес / локация
            "UF_CRM_OBJECT_TYPE",     # Тип объекта
            "UF_CRM_AREA_SQM",        # Площадь м²
            "UF_CRM_SURFACE_TYPE",    # Тип поверхности
            "UF_CRM_MATERIAL_NAME",   # Материал
            "UF_CRM_COLOR_CODE",      # Цвет и код
        ]
        for key in field_keys:
            val = source_deal.get(key)
            if val:
                source_fields[key] = val

        # Создаём новую сделку в воронке Нанесение
        new_deal_fields = {
            "TITLE": f"[Нанесение] {title}",
            "CATEGORY_ID": nanesenie_cat_id,
            "STAGE_ID": first_stage,
            "CONTACT_ID": contact_id,
            "COMPANY_ID": company_id,
            "ASSIGNED_BY_ID": resp,
            "COMMENTS": f"Создано автоматически из сделки продажи #{source_id}\nОригинальная сделка: {title}",
            **source_fields
        }

        result = b24("crm.deal.add", {"fields": new_deal_fields})

        if result:
            new_deal_id = result
            print(f"[NANESENIE] ✓ Создана сделка #{new_deal_id} из продажи #{source_id}")

            # Уведомляем ответственного за нанесение
            notify(USERS["sklad"], 
                f"🏗️ Новый объект для нанесения!\nСделка: {title}\nКлиент перешёл из продаж в нанесение.\nСделка #{new_deal_id}",
                new_deal_id)
            notify(USERS["rop"],
                f"✓ Создана сделка в Нанесении\nИз: {title} (#{source_id})\nНовая сделка: #{new_deal_id}",
                new_deal_id)

            # Задача на подтверждение ТЗ
            create_task(
                f"Подтвердить ТЗ с клиентом — {title}",
                resp,
                new_deal_id,
                f"Сделка создана автоматически после продажи материала.\nПодтвердить техническое задание с клиентом.\nИсходная сделка продаж: #{source_id}",
                48
            )
        else:
            print(f"[NANESENIE] ✗ Ошибка создания сделки")

    except Exception as e:
        print(f"[ERROR] create_nanesenie_deal: {e}")

def on_new_deal(deal):
    try:
        did = deal["ID"]
        title, contact = deal_info(deal)
        resp = int(deal.get("ASSIGNED_BY_ID") or USERS["rop"])
        print(f"[NEW DEAL] {title} | resp={resp}")

        create_task(
            f"Связаться с клиентом — {title}",
            resp, did,
            f"Новый лид! Клиент: {contact}\nСвяжись в течение 15 минут.",
            0.25
        )
        notify(resp, f"Новый лид!\nКлиент: {contact}\nСделка: {title}\nСвяжись в течение 15 минут!", did)

        def escalate():
            time.sleep(900)
            d = get_deal(did)
            if d and d.get("STAGE_ID") == deal.get("STAGE_ID"):
                notify(USERS["rop"], f"Лид без ответа 15 минут!\nКлиент: {contact}\nСделка: {title}", did)
        threading.Thread(target=escalate, daemon=True).start()

    except Exception as e:
        print(f"[ERROR] on_new_deal: {e}")

def on_stage_change(deal, old_stage_id, new_stage_id):
    try:
        did = deal["ID"]
        title, contact = deal_info(deal)
        resp = int(deal.get("ASSIGNED_BY_ID") or USERS["rop"])
        new_stage = get_stage_name(new_stage_id)
        print(f"[STAGE] {title}: {old_stage_id} -> '{new_stage}' ({new_stage_id}) pipeline={pipeline}")

        if new_stage in ["1st message no answer", "2nd message no answer", "3rd message no answer"]:
            def bot_esc(s=new_stage, sid=new_stage_id):
                time.sleep(86400)
                d = get_deal(did)
                if d and d.get("STAGE_ID") == sid:
                    notify(resp, f"Клиент не отвечает ({s})\nКлиент: {contact}\nПопробуй другой канал.", did)
            threading.Thread(target=bot_esc, daemon=True).start()

        elif new_stage == "Квалификация":
            create_task(f"Квалификация клиента — {title}", resp, did,
                f"Клиент: {contact}\nТип клиента, объект, объём м², что красим, стадия проекта.", 24)
            def check(sid=new_stage_id):
                time.sleep(86400)
                d = get_deal(did)
                if d and d.get("STAGE_ID") == sid:
                    notify(USERS["rop"], f"Сделка зависла на квалификации >1 дня\nКлиент: {contact}", did)
            threading.Thread(target=check, daemon=True).start()

        elif new_stage == "Согласование":
            create_task(f"Согласовать детали — {title}", resp, did,
                f"Клиент: {contact}\nВнутри/снаружи, мокрые зоны, объём, цвет, бюджет.", 24)

        elif new_stage == "Коммерческое предложение":
            create_task(f"Подготовить КП — {title}", resp, did,
                f"Клиент: {contact}\nОбъём + 10%. До 5000м² retail. От 5000м² developer.\nОтправить: вёдра + цена + логика.", 4)
            notify(resp, f"КП нужно отправить в течение 4 часов\nКлиент: {contact}", did)
            def kp(sid=new_stage_id):
                time.sleep(14400)
                d = get_deal(did)
                if d and d.get("STAGE_ID") == sid:
                    notify(resp, f"КП не отправлено 4 часа!\nКлиент: {contact}", did)
                    time.sleep(14400)
                    d = get_deal(did)
                    if d and d.get("STAGE_ID") == sid:
                        notify(USERS["rop"], f"КП не отправлено 8 часов!\nСделка: {title}", did)
            threading.Thread(target=kp, daemon=True).start()

        elif new_stage == "Согласование и дожим":
            create_task(f"Получить ответ по КП — {title}", resp, did,
                f"Клиент: {contact}\nОтработать возражения. Follow-up.", 72)
            def dojim(sid=new_stage_id):
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

        elif new_stage == "Оплата":
            create_task(f"Проконтролировать оплату — {title}", USERS["buh"], did,
                f"Клиент: {contact}\nПри оплате — выдать Receipt, перевести в Колеровку.", 48)
            def pay(sid=new_stage_id):
                time.sleep(82800)
                d = get_deal(did)
                if d and d.get("STAGE_ID") == sid:
                    notify(resp, f"Напомни клиенту об оплате\nКлиент: {contact}", did)
                    time.sleep(3600)
                    d = get_deal(did)
                    if d and d.get("STAGE_ID") == sid:
                        notify_many([resp, USERS["buh"]], f"ПРОСРОЧКА ОПЛАТЫ!\nСделка: {title}\nКлиент: {contact}", did)
            threading.Thread(target=pay, daemon=True).start()

        elif new_stage == "Колеровка":
            create_task(f"Списать товар → колеровка — {title}", USERS["sklad"], did,
                f"Клиент: {contact}\nСписать товар, передать колеровщику с кодом цвета.", 8)
            def koler():
                time.sleep(7200)
                create_task(f"Выполнить колеровку — {title}", USERS["koler"], did,
                    f"Клиент: {contact}\nВыполнить по коду цвета, уведомить менеджера.", 8)
            threading.Thread(target=koler, daemon=True).start()

        elif new_stage in ["Отгрузка и логистика", "Реализация и отгрузка", "Реализация"]:
            create_task(f"Организовать отгрузку — {title}", USERS["logist"], did,
                f"Клиент: {contact}\nДоставка или самовывоз. Уведомить клиента.", 8)
            notify(resp, f"Товар готов к отгрузке!\nКлиент: {contact}", did)

        elif new_stage in ["Завершено", "Закрыто успешно"]:
            create_task(f"Запросить отзыв — {title}", resp, did,
                f"Клиент: {contact}\nОтзыв + фото через 2-3 дня.", 72)
            notify(resp, f"Сделка закрыта!\nКлиент: {contact}\nЗапроси отзыв.", did)

        elif new_stage in ["Закрыто без продажи", "Закрыто отказ"]:
            create_task(f"Заполнить причину отказа — {title}", resp, did,
                f"ОБЯЗАТЕЛЬНО: поле «Причина отказа».\nКлиент: {contact}", 4)
            def retry():
                time.sleep(86400 * 30)
                create_task(f"Повторный контакт — {title}", resp, did,
                    f"30 дней прошло.\nКлиент: {contact}", 48)
            threading.Thread(target=retry, daemon=True).start()

        elif new_stage == "Договор подписан":
            create_task(f"Подтвердить дату старта — {title}", resp, did,
                f"Клиент: {contact}\nПодтвердить дату работ.", 24)
            create_task(f"Проверить документы — {title}", USERS["buh"], did,
                f"Договор на нанесение. Клиент: {contact}", 24)

        elif new_stage == "Выход бригады":
            create_task(f"Выход бригады — {title}", resp, did,
                f"Клиент: {contact}\nМастера, дата старта, готовность.", 2)

        elif new_stage == "Сдача объекта":
            create_task(f"Подписать акт и фото — {title}", resp, did,
                f"Клиент: {contact}\nПодписать акт. Фото. Отзыв.", 24)

        elif new_stage == "Обучение":
            create_task(f"Провести обучение дилера — {title}", resp, did,
                f"Дилер: {contact}\nНазначить дату и провести обучение.", 72)

    except Exception as e:
        print(f"[ERROR] on_stage_change: {e}")

# Маппинг: ключевое слово в названии задачи -> следующая стадия
# Маппинг: ключевое слово в названии задачи -> следующая стадия (простые строки!)
TASK_NEXT_STAGE = {
    "Связаться с клиентом":      "Квалификация",
    "Квалификация клиента":      "Согласование",
    "Квалификацию клиента":      "Согласование",
    "Согласовать детали":        "Коммерческое предложение",
    "Подготовить КП":            "Согласование и дожим",
    "Получить ответ по КП":      "Счёт и договор",
    "Выставить Invoice":         "Оплата",
    "Проконтролировать оплату":  "Receipt",
    "Выдать Receipt":            "Колеровка",
    "Выполнить колеровку":       "Отгрузка и логистика",
    "Организовать отгрузку":     "Завершено",
    "Подтвердить дату старта":   "Подготовка объекта",
    "Провести инспекцию":        "Объект готов",
    "Тестовую стену":            "Тест согласован",
    "Выставить счёт на аванс":   "Ожидание оплаты (аванс)",
    "Проконтролировать аванс":   "Подготовка к выходу",
    "Подготовить выход":         "В работе",
    "Выход бригады":             "В работе",
    "Выставить промежуточный":   "80% выполнено",
    "Финальная приемка":         "Приемка",
    "Подписать акт":             "Закрыта успешно",
}

def ensure_stages_loaded():
    """Загружает стадии если они пустые"""
    if not PIPELINE_STAGES or all(len(v)==0 for v in PIPELINE_STAGES.values()):
        print("[STAGES] Стадии пустые — загружаем...")
        load_stages()

def check_task_status(task_id):
    """Запрашивает задачу через API и проверяет её статус"""
    ensure_stages_loaded()
    try:
        result = b24("tasks.task.get", {
            "taskId": task_id,
            "select": ["ID","TITLE","STATUS","UF_CRM_TASK","REAL_STATUS","STAGE_ID"]
        })
        if not result:
            print(f"[TASK CHECK] id={task_id} — нет результата от API")
            return
        task = result.get("task", result)
        # API возвращает поля в нижнем регистре!
        status = str(task.get("status") or task.get("STATUS") or "")
        title = str(task.get("title") or task.get("TITLE") or "")
        # UF_CRM_TASK тоже может быть в нижнем регистре
        crm_links = task.get("ufCrmTask") or task.get("UF_CRM_TASK") or task.get("uf_crm_task") or []
        # Добавляем в task для on_task_complete
        task["_status"] = status
        task["_title"] = title
        task["_crm_links"] = crm_links
        print(f"[TASK CHECK] id={task_id} status={status} title={title[:40]}")
        if status == "5":
            on_task_complete(task)
    except Exception as e:
        print(f"[ERROR] check_task_status: {e}")

def on_task_complete(task):
    try:
        if isinstance(task, (str, int)):
            result = b24("tasks.task.get", {"taskId": task, "select": ["ID","TITLE","STATUS","UF_CRM_TASK"]})
            if not result:
                return
            task = result.get("task", result)
            task["_status"] = str(task.get("status") or "")
            task["_title"] = str(task.get("title") or "")
            task["_crm_links"] = task.get("ufCrmTask") or task.get("UF_CRM_TASK") or []
        title = task.get("_title") or task.get("title") or task.get("TITLE") or ""
        crm_links = task.get("_crm_links") or task.get("ufCrmTask") or task.get("UF_CRM_TASK") or []
        print(f"[TASK DONE] {title} | crm={crm_links}")

        # Находим привязанную сделку
        deal_id = None
        for link in (crm_links or []):
            if str(link).startswith("D_"):
                deal_id = str(link).replace("D_", "")
                break

        if not deal_id:
            return

        deal = get_deal(deal_id)
        if not deal:
            return

        # Определяем воронку по CATEGORY_ID сделки
        cat_id = str(deal.get("CATEGORY_ID", "0"))
        pipeline = "main"
        if cat_id != "0":
            # Сначала ищем в загруженных воронках
            for pip_name, stages in PIPELINE_STAGES.items():
                for stage_name, stage_id in stages.items():
                    if stage_id and stage_id.startswith(f"C{cat_id}:"):
                        pipeline = pip_name
                        break
                if pipeline != "main":
                    break
            # Если не нашли - запрашиваем из API
            if pipeline == "main":
                pipelines = b24("crm.dealcategory.list") or []
                for p in pipelines:
                    if str(p["ID"]) == cat_id:
                        pipeline = p["NAME"]
                        break

        print(f"[TASK DONE] pipeline={pipeline} cat_id={cat_id}")
        print(f"[TASK DONE] available stages: {list(PIPELINE_STAGES.get(pipeline, {}).keys())}")

        # Ищем совпадение в маппинге
        next_stage = None
        for keyword, stage in TASK_NEXT_STAGE.items():
            if keyword.lower() in title.lower():
                # stage всегда строка
                next_stage = str(stage)
                print(f"[TASK DONE] Найден маппинг: '{keyword}' -> '{next_stage}'")
                break

        if not next_stage:
            print(f"[TASK DONE] Нет маппинга для: {title}")
            return

        # Ищем стадию сначала в текущей воронке, потом во всех
        stage_id = PIPELINE_STAGES.get(pipeline, {}).get(next_stage)
        if not stage_id:
            # Ищем во всех воронках
            for pip_name, stages in PIPELINE_STAGES.items():
                if next_stage in stages:
                    stage_id = stages[next_stage]
                    print(f"[TASK DONE] Стадия найдена в {pip_name}")
                    break

        if not stage_id:
            print(f"[TASK DONE] Стадия не найдена нигде: {next_stage}")
            return

        result = move_deal(deal_id, stage_id)
        print(f"[TASK DONE] Сделка {deal_id} -> {next_stage} ({stage_id}) result={result}")
        if result:
            # Сразу создаём задачу для новой стадии
            updated_deal = get_deal(deal_id)
            if updated_deal:
                threading.Thread(
                    target=on_stage_change,
                    args=(updated_deal, deal.get("STAGE_ID",""), stage_id),
                    daemon=True
                ).start()

    except Exception as e:
        print(f"[ERROR] on_task_complete: {e}")

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
    elif event == "ONTASKUPDATE":
        # Получаем ID из всех возможных мест
        task_id = (data.get("data[FIELDS_AFTER][ID]") or 
                   data.get("data[FIELDS][ID]") or
                   data.get("data[ID]"))
        # Статус может прийти в разных полях или вообще не прийти
        # Поэтому запрашиваем задачу напрямую через API
        if task_id:
            threading.Thread(target=check_task_status, args=(task_id,), daemon=True).start()
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
        return jsonify({"error": f"Stage not found: {stage_name}"}), 404
    result = move_deal(did, sid)
    return jsonify({"status": "ok", "stage_id": sid})

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
    # event.bind не работает через incoming webhook — вебхуки настраиваются вручную в Битрикс24
    return jsonify({"status": "ok", "stages": PIPELINE_STAGES, 
        "note": "Стадии загружены. Вебхуки настрой вручную в Битрикс24 -> Разработчикам -> Исходящие вебхуки"})

@app.route("/", methods=["GET"])
def index():
    return jsonify({"name": "Pratta Automation", "status": "running", 
                    "stages_loaded": sum(len(v) for v in PIPELINE_STAGES.values())})

# Загружаем стадии при старте модуля (работает и с gunicorn)
def startup():
    print("Pratta Thailand — Bitrix24 Automation — Loading stages...")
    load_stages()

startup()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
