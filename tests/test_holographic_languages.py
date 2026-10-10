"""Multilingual quality: English, Russian and Chinese stores of everyday facts, labelled questions, chit-chat that
must recall nothing, entity probes and a contradiction on write. Thresholds are the measured level of 0.4.2, so a
change that makes a language worse fails here."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("snowballstemmer")

from hermes_plugin_holographic import HolographicMemoryProvider

# key -> (fact text, declared entities)
CORPUS = {
"en": {
 "anton_bike": ("Anton rides a Honda Forza scooter around Pattaya", ["Anton"]),
 "anton_coffee": ("Anton drinks his coffee black without sugar", ["Anton"]),
 "ksenia_job": ("Ksenia runs HR24, a recruiting agency for sales specialists", ["Ksenia"]),
 "ksenia_search": ("Ksenia is looking for a sales manager for a client in Dubai", ["Ksenia"]),
 "eva_school": ("Eva studies at an international school in Jomtien", ["Eva"]),
 "eva_allergy": ("Eva is allergic to peanuts", ["Eva"]),
 "eva_swim": ("Eva has swimming lessons every Saturday morning", ["Eva"]),
 "move": ("The family plans to move to Chiang Mai in spring 2027", []),
 "deploy": ("The website is deployed with GitHub Actions on every merge to main", []),
 "server": ("The production server hr-ovh runs Debian 12 with 8 GB of RAM", []),
 "backup": ("Database backups of hr-ovh run every night at 03:00", []),
 "meetup": ("The IT meetup in Pattaya starts at 22:00 on Fridays", []),
 "dj": ("DJ Artemida plays melodic techno at the Friday party", ["Artemida"]),
 "copilot": ("HR24 Copilot transcribes interviews and compares candidates with the vacancy", []),
 "editor": ("Anton prefers short answers without bullet lists", ["Anton"]),
 "budget": ("The monthly marketing budget is 500 dollars", []),
 "dentist": ("Eva's dentist appointment is on October 20", ["Eva"]),
 "cat": ("The family cat is a British shorthair called Mia", ["Mia"]),
 "python": ("Scripts unrelated to Hermes should run with Python 3.12", []),
 "laptop": ("The work laptop has only 8 GB of memory, so no local LLMs", []),
},
"ru": {
 "anton_bike": ("Антон ездит на байке Honda Forza по Паттайе", ["Антон"]),
 "anton_coffee": ("Антон пьёт чёрный кофе без сахара", ["Антон"]),
 "ksenia_job": ("Ксюша руководит рекрутинговым агентством HR24", ["Ксюша"]),
 "ksenia_search": ("Ксюша ищет менеджера по продажам для клиента в Дубае", ["Ксюша"]),
 "eva_school": ("Ева учится в международной школе в Джомтьене", ["Ева"]),
 "eva_allergy": ("У Евы аллергия на арахис", ["Ева"]),
 "eva_swim": ("По субботам утром у Евы плавание", ["Ева"]),
 "move": ("Семья планирует переезд в Чиангмай весной 2027 года", []),
 "deploy": ("Сайт выкладывается через GitHub Actions при каждом мерже в main", []),
 "server": ("Боевой сервер hr-ovh работает на Debian 12 с 8 ГБ памяти", []),
 "backup": ("Резервные копии базы на hr-ovh делаются каждую ночь в 03:00", []),
 "meetup": ("IT-тусовка в Паттайе начинается в 22:00 по пятницам", []),
 "dj": ("Диджей Артемида играет мелодик-техно на пятничной вечеринке", ["Артемида"]),
 "copilot": ("HR24 Copilot расшифровывает интервью и сравнивает кандидатов с вакансией", []),
 "editor": ("Антон предпочитает короткие ответы без списков", ["Антон"]),
 "budget": ("Месячный бюджет на маркетинг составляет 500 долларов", []),
 "dentist": ("Приём у стоматолога для Евы назначен на 20 октября", ["Ева"]),
 "cat": ("Семейная кошка — британская короткошёрстная по имени Мия", ["Мия"]),
 "python": ("Скрипты не для Hermes запускаются на Python 3.12", []),
 "laptop": ("На рабочем ноутбуке всего 8 ГБ памяти, поэтому локальные модели не запускаем", []),
},
"zh": {
 "anton_bike": ("安东在芭提雅骑本田Forza摩托车", ["安东"]),
 "anton_coffee": ("安东喝黑咖啡，不加糖", ["安东"]),
 "ksenia_job": ("克修莎经营一家招聘销售人才的猎头公司HR24", ["克修莎"]),
 "ksenia_search": ("克修莎正在为迪拜的客户招聘销售经理", ["克修莎"]),
 "eva_school": ("伊娃在宗甸的国际学校上学", ["伊娃"]),
 "eva_allergy": ("伊娃对花生过敏", ["伊娃"]),
 "eva_swim": ("伊娃每周六上午上游泳课", ["伊娃"]),
 "move": ("全家计划在2027年春天搬到清迈", []),
 "deploy": ("网站每次合并到main分支时通过GitHub Actions自动部署", []),
 "server": ("生产服务器hr-ovh运行Debian 12，内存8GB", []),
 "backup": ("hr-ovh的数据库每天凌晨3点备份", []),
 "meetup": ("芭提雅的IT聚会每周五晚上10点开始", []),
 "dj": ("DJ阿尔忒弥达在周五派对上播放旋律科技音乐", ["阿尔忒弥达"]),
 "copilot": ("HR24 Copilot可以转写面试录音并把候选人和职位要求进行比较", []),
 "editor": ("安东喜欢简短的回答，不要列表", ["安东"]),
 "budget": ("每月的市场营销预算是500美元", []),
 "dentist": ("伊娃的牙医预约在10月20日", ["伊娃"]),
 "cat": ("家里的猫是一只英国短毛猫，名叫米娅", ["米娅"]),
 "python": ("与Hermes无关的脚本使用Python 3.12运行", []),
 "laptop": ("工作笔记本只有8GB内存，所以不运行本地大模型", []),
},
}

# (query, relevant keys) — empty set = chit-chat / off-topic: nothing should be recalled before the turn
QUERIES = {
"en": [
 ("what scooter does Anton ride?", {"anton_bike"}), ("how does Anton take his coffee", {"anton_coffee"}),
 ("who is Ksenia hiring for in Dubai?", {"ksenia_search"}), ("what does HR24 do", {"ksenia_job", "copilot"}),
 ("which school does Eva go to", {"eva_school"}), ("is Eva allergic to anything?", {"eva_allergy"}),
 ("when are Eva's swimming lessons", {"eva_swim"}), ("when is the family moving to Chiang Mai", {"move"}),
 ("how is the website deployed", {"deploy"}), ("how much RAM does hr-ovh have", {"server"}),
 ("when do the backups run", {"backup"}), ("what time does the meetup start on Friday", {"meetup"}),
 ("who plays at the Friday party", {"dj"}), ("what does Copilot do with interviews", {"copilot"}),
 ("how should answers to Anton be formatted", {"editor"}), ("what is our marketing budget", {"budget"}),
 ("when is Eva's dentist appointment", {"dentist"}), ("what breed is the cat", {"cat"}),
 ("which Python version for scripts", {"python"}), ("can I run a local LLM on the laptop", {"laptop"}),
 ("ok thanks", set()), ("hi there!", set()), ("sounds good, go ahead", set()),
 ("what is the capital of Australia?", set()), ("write a haiku about autumn", set()),
],
"ru": [
 ("на каком байке ездит Антон?", {"anton_bike"}), ("какой кофе пьёт Антон", {"anton_coffee"}),
 ("кого Ксюша ищет в Дубай?", {"ksenia_search"}), ("чем занимается агентство Ксюши", {"ksenia_job"}),
 ("в какой школе учится Ева", {"eva_school"}), ("на что у Евы аллергия?", {"eva_allergy"}),
 ("когда у Евы плавание", {"eva_swim"}), ("когда переезжаем в Чиангмай", {"move"}),
 ("как выкладывается сайт", {"deploy"}), ("сколько памяти на сервере hr-ovh", {"server"}),
 ("во сколько делаются бэкапы базы", {"backup"}), ("во сколько начинается тусовка в пятницу", {"meetup"}),
 ("кто играет на пятничной вечеринке", {"dj"}), ("что умеет HR24 Copilot", {"copilot"}),
 ("как отвечать Антону", {"editor"}), ("какой у нас бюджет на маркетинг", {"budget"}),
 ("когда Еве к стоматологу", {"dentist"}), ("какой породы кошка", {"cat"}),
 ("на каком питоне запускать скрипты", {"python"}), ("можно ли запустить локальную модель на ноутбуке", {"laptop"}),
 ("ок, спасибо", set()), ("привет!", set()), ("да, давай", set()),
 ("какая столица Австралии?", set()), ("напиши стихотворение про осень", set()),
],
"zh": [
 ("安东骑什么摩托车？", {"anton_bike"}), ("安东喝什么咖啡", {"anton_coffee"}),
 ("克修莎在迪拜招聘什么职位？", {"ksenia_search"}), ("HR24是做什么的", {"ksenia_job", "copilot"}),
 ("伊娃在哪个学校上学", {"eva_school"}), ("伊娃对什么过敏？", {"eva_allergy"}),
 ("伊娃什么时候上游泳课", {"eva_swim"}), ("我们什么时候搬到清迈", {"move"}),
 ("网站是怎么部署的", {"deploy"}), ("hr-ovh有多少内存", {"server"}),
 ("数据库什么时候备份", {"backup"}), ("周五的聚会几点开始", {"meetup"}),
 ("周五派对上谁打碟", {"dj"}), ("HR24 Copilot能做什么", {"copilot"}),
 ("回答安东应该用什么格式", {"editor"}), ("市场营销预算是多少", {"budget"}),
 ("伊娃的牙医预约是哪天", {"dentist"}), ("家里的猫是什么品种", {"cat"}),
 ("脚本用哪个Python版本", {"python"}), ("笔记本能运行本地大模型吗", {"laptop"}),
 ("好的，谢谢", set()), ("你好！", set()), ("可以，继续吧", set()),
 ("澳大利亚的首都是哪里？", set()), ("写一首关于秋天的诗", set()),
],
}

PROBES = {"en": [("Anton", {"anton_bike", "anton_coffee", "editor"}), ("Eva", {"eva_school", "eva_allergy", "eva_swim", "dentist"})],
          "ru": [("Антон", {"anton_bike", "anton_coffee", "editor"}), ("Ева", {"eva_school", "eva_allergy", "eva_swim", "dentist"})],
          "zh": [("安东", {"anton_bike", "anton_coffee", "editor"}), ("伊娃", {"eva_school", "eva_allergy", "eva_swim", "dentist"})]}
CONFLICTS = {"en": ("anton_coffee", "Anton drinks his coffee with milk and sugar"),
             "ru": ("anton_coffee", "Антон пьёт кофе с молоком и сахаром"),
             "zh": ("anton_coffee", "安东喝咖啡加牛奶和糖")}




@pytest.fixture(params=["en", "ru", "zh"])
def lang_store(request, tmp_path):
    lang = request.param
    provider = HolographicMemoryProvider(config={"db_path": str(tmp_path / f"{lang}.db"), "hrr_dim": 1024})
    provider.initialize(session_id=lang)
    back = {}
    for key, (text, entities) in CORPUS[lang].items():
        result = json.loads(provider.handle_tool_call("fact_store", {"action": "add", "content": text, "entities": entities}))
        back[result["fact_id"]] = key
    yield lang, provider, back
    provider.shutdown()


def _call(provider, **args):
    return json.loads(provider.handle_tool_call("fact_store", args))


def test_questions_find_their_fact_and_chit_chat_recalls_nothing(lang_store):
    lang, provider, back = lang_store
    hit5 = recalled = asked = quiet = negatives = 0
    for i, (query, relevant) in enumerate(QUERIES[lang]):
        provider.on_session_switch(f"q{i}", reset=True)
        found = {back[r["fact_id"]] for r in _call(provider, action="search", query=query, limit=5)["results"]}
        block = provider.prefetch(query)
        injected = {back[int(line.split()[1][1:])] for line in block.splitlines()[1:]} if block else set()
        if relevant:
            asked += 1
            hit5 += bool(found & relevant)
            recalled += bool(injected & relevant)
        else:
            negatives += 1
            quiet += not injected
    assert hit5 / asked >= 0.95, f"{lang}: search found {hit5}/{asked}"
    assert recalled / asked >= 0.95, f"{lang}: recall before the turn {recalled}/{asked}"
    assert quiet == negatives, f"{lang}: chit-chat recalled facts in {negatives - quiet}/{negatives}"


def test_probe_reason_and_conflict(lang_store):
    lang, provider, back = lang_store
    for name, expected in PROBES[lang]:
        assert {back[r["fact_id"]] for r in _call(provider, action="probe", entity=name, limit=10)["results"]} == expected
    word = {"en": "allergy", "ru": "аллергия", "zh": "过敏"}[lang]
    assert [back[r["fact_id"]] for r in _call(provider, action="reason", entities=[PROBES[lang][1][0], word])["results"]] == ["eva_allergy"]
    old_key, new_text = CONFLICTS[lang]
    added = _call(provider, action="add", content=new_text, entities=[PROBES[lang][0][0]])
    assert old_key in [back.get(c["fact_id"]) for c in added.get("possible_conflicts", [])]
