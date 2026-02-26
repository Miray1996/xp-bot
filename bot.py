import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import time
import os
import psycopg2

TOKEN = os.getenv("TOKEN")

bot = telebot.TeleBot(TOKEN)

# ================= БАЗА =================

DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    saved_xp INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS skills (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    name TEXT,
    xp INTEGER DEFAULT 0
)
""")



# ================= СОСТОЯНИЕ =================

user_states = {}
cooldowns = {}
active_messages = {}
skill_prompt_messages = {}

COOLDOWN_TIME = 120
MAX_FAST_CLICKS = 10

# ================= УТИЛИТЫ =================

def delete_message(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

def remember_message(user_id, chat_id, message_id):
    active_messages[user_id] = (chat_id, message_id)

def delete_last_message(user_id):
    if user_id in active_messages:
        chat_id, message_id = active_messages[user_id]
        delete_message(chat_id, message_id)

def remember_skill_prompt(user_id, chat_id, message_id):
    skill_prompt_messages[user_id] = (chat_id, message_id)

def delete_skill_prompt(user_id):
    if user_id in skill_prompt_messages:
        chat_id, message_id = skill_prompt_messages[user_id]
        delete_message(chat_id, message_id)

def has_skills(user_id):
    cursor.execute("SELECT COUNT(*) FROM skills WHERE user_id=%s", (user_id,))
    return cursor.fetchone()[0] > 0

def plural_skills(n):
    if n % 10 == 1 and n % 100 != 11:
        return "навык"
    elif 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "навыка"
    else:
        return "навыков"

def check_cooldown(user_id):
    now = time.time()

    if user_id not in cooldowns:
        cooldowns[user_id] = {"count": 0, "blocked_until": 0}

    data = cooldowns[user_id]

    if now < data["blocked_until"]:
        return False

    if now >= data["blocked_until"]:
        data["count"] = 0
        data["blocked_until"] = 0

    data["count"] += 1

    if data["count"] > MAX_FAST_CLICKS:
        data["blocked_until"] = now + COOLDOWN_TIME
        return False

    return True

# ================= START =================

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id

    if has_skills(user_id):
        bot.send_message(
            message.chat.id,
            "У тебя уже есть созданная ветка навыков.\n\nЖми /listskills чтобы посмотреть их.🚀"
        )
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Создать", callback_data="create_skills"))

    msg = bot.send_message(
        message.chat.id,
        "Прокачка персонажа начата.\n\nТеперь создай навыки, на них будет поступать XP.✅",
        reply_markup=markup
    )

    remember_message(user_id, message.chat.id, msg.message_id)

# ================= СОЗДАНИЕ =================

@bot.callback_query_handler(func=lambda call: call.data == "create_skills")
def choose_skill_count(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    markup = InlineKeyboardMarkup(row_width=4)
    buttons = [InlineKeyboardButton(str(i), callback_data=f"skillcount_{i}") for i in range(1,9)]
    markup.add(*buttons)

    msg = bot.send_message(call.message.chat.id, "Выбери кол-во навыков.", reply_markup=markup)
    remember_message(user_id, call.message.chat.id, msg.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("skillcount_"))
def create_skills(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    count = int(call.data.split("_")[1])

    user_states[user_id] = {
        "creating": True,
        "remaining": count,
        "total": count
    }

    bot.send_message(call.message.chat.id, "Лучше давать простые названия навыкам.✏️")

    prompt = bot.send_message(call.message.chat.id, "Дай название первому навыку.")
    remember_skill_prompt(user_id, call.message.chat.id, prompt.message_id)

    bot.answer_callback_query(call.id)

# ================= SAVE SKILL / DELETE XP =================

@bot.message_handler(func=lambda message: message.from_user.id in user_states)
def save_skill(message):
    user_id = message.from_user.id
    state = user_states[user_id]
    # ---- RENAME ----
    if "renaming" in state:
        new_name = message.text
        skill_id = state["skill_id"]

        cursor.execute("UPDATE skills SET name=%s WHERE id=%s", (new_name, skill_id))
        

        bot.delete_message(message.chat.id, message.message_id)
        delete_skill_prompt(user_id)
        del user_states[user_id]

        bot.send_message(
            message.chat.id,
            f'Теперь твой навык называется "{new_name}"✅.'
        )
        return
    # ---- Удаление XP ----
    if "deleting_xp" in state:
        if not message.text.isdigit():
            bot.delete_message(message.chat.id, message.message_id)
            return

        amount = int(message.text)
        skill_id = state["skill_id"]

        cursor.execute("SELECT xp FROM skills WHERE id=%s", (skill_id,))
        current_xp = cursor.fetchone()[0]

        new_xp = max(0, current_xp - amount)

        cursor.execute("UPDATE skills SET xp=%s WHERE id=%s", (new_xp, skill_id))
        

        bot.delete_message(message.chat.id, message.message_id)
        delete_skill_prompt(user_id)
        del user_states[user_id]

        bot.send_message(message.chat.id, f"Теперь у твоего навыка {new_xp} XP.")
        return

    # ---- Добавление нового навыка ----
    if "adding" in state:
        cursor.execute("""
INSERT INTO users (user_id, username)
VALUES (%s, %s)
ON CONFLICT (user_id) DO NOTHING
""", (user_id, message.from_user.username))

        cursor.execute("""
INSERT INTO skills (user_id, name, xp)
VALUES (%s, %s, 0)
""", (user_id, message.text))
        

        bot.delete_message(message.chat.id, message.message_id)
        delete_skill_prompt(user_id)
        del user_states[user_id]

        bot.send_message(message.chat.id, "Новый навык успешно добавлен.🎉")
        return

    # ---- Создание при start ----
    cursor.execute("""
INSERT INTO users (user_id, username)
VALUES (%s, %s)
ON CONFLICT (user_id) DO NOTHING
""", (user_id, message.from_user.username))

    cursor.execute("""
INSERT INTO skills (user_id, name, xp)
VALUES (%s, %s, 0)
""", (user_id, message.text))
    

    bot.delete_message(message.chat.id, message.message_id)
    delete_skill_prompt(user_id)

    state["remaining"] -= 1

    if state["remaining"] > 0:
        prompt = bot.send_message(message.chat.id, "Дай название следующему навыку.")
        remember_skill_prompt(user_id, message.chat.id, prompt.message_id)
    else:
        total = state["total"]
        del user_states[user_id]
        bot.send_message(
            message.chat.id,
            f"Персонаж получил {total} {plural_skills(total)}.\n\nЖми /listskills для просмотра своих навыков.🎉"
        )

# ================= LIST SKILLS (БЕЗ КНОПОК) =================

@bot.message_handler(commands=['listskills'])
def list_skills(message):
    user_id = message.from_user.id

    cursor.execute("SELECT name, xp FROM skills WHERE user_id=%s", (user_id,))
    skills = cursor.fetchall()

    if not skills:
        bot.send_message(message.chat.id, "У тебя пока нет навыков.")
        return

    text = "Твои навыки.\n\n"
    for i, skill in enumerate(skills, 1):
        text += f"{i}. {skill[0]} — {skill[1]} XP\n"

    text += "\nЕсли хочешь увеличить XP, жми на - /addxp.🚀"

    bot.send_message(message.chat.id, text)

# ================= ADDXP =================

@bot.message_handler(commands=['addxp'])
def addxp(message):
    user_id = message.from_user.id

    cursor.execute("SELECT id, name, xp FROM skills WHERE user_id=%s", (user_id,))
    skills = cursor.fetchall()

    if not skills:
        bot.send_message(message.chat.id, "Сначала создай навыки через /start.")
        return

    text = "Твои навыки.\n\n"
    for i, skill in enumerate(skills, 1):
        text += f"{i}. {skill[1]} — {skill[2]} XP\n"

    text += "\nВыбери навык ниже, чтобы добавить XP.🚀"

    markup = InlineKeyboardMarkup(row_width=2)
    for skill in skills:
        markup.add(InlineKeyboardButton(skill[1], callback_data=f"selectskill_{skill[0]}"))

    msg = bot.send_message(message.chat.id, text, reply_markup=markup)
    remember_message(user_id, message.chat.id, msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("selectskill_"))
def skill_menu(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    skill_id = int(call.data.split("_")[1])
    cursor.execute("SELECT name, xp FROM skills WHERE id=%s", (skill_id,))
    skill = cursor.fetchone()

    if not skill:
        bot.answer_callback_query(call.id)
        return

    markup = InlineKeyboardMarkup(row_width=3)
    for value in [1,5,10,20,50,75,100]:
        markup.add(InlineKeyboardButton(f"+{value}", callback_data=f"addxp_{skill_id}_{value}"))

    msg = bot.send_message(
        call.message.chat.id,
        f"{skill[0]}.🔥\n\nТекущий XP: {skill[1]}",
        reply_markup=markup
    )

    remember_message(user_id, call.message.chat.id, msg.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("addxp_"))
def add_xp(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    _, skill_id, xp = call.data.split("_")
    skill_id = int(skill_id)
    xp = int(xp)

    if not check_cooldown(user_id):
        bot.answer_callback_query(call.id, "Подожди 2 минуты.⏳", show_alert=True)
        return

    cursor.execute("SELECT xp, name FROM skills WHERE id=%s", (skill_id,))
    old_xp, skill_name = cursor.fetchone()

    new_xp = old_xp + xp
    cursor.execute("UPDATE skills SET xp=%s WHERE id=%s", (new_xp, skill_id))
    

    bot.send_message(
        call.message.chat.id,
        f'Твой навык "{skill_name}" получил {xp} XP.\n\n'
        f"Общий XP стал {new_xp}, красавчик!💎"
    )

    bot.answer_callback_query(call.id)

# ================= DELETE XP =================

@bot.message_handler(commands=['delexper'])
def delete_experience(message):
    user_id = message.from_user.id

    if not has_skills(user_id):
        bot.send_message(message.chat.id, "Сначала нажми первую команду /start.")
        return

    cursor.execute("SELECT id, name FROM skills WHERE user_id=%s", (user_id,))
    skills = cursor.fetchall()

    markup = InlineKeyboardMarkup(row_width=2)
    for skill in skills:
        markup.add(InlineKeyboardButton(skill[1], callback_data=f"choose_delxp_{skill[0]}"))

    msg = bot.send_message(
        message.chat.id,
        "Из какого навыка требуется убрать XP?",
        reply_markup=markup
    )

    remember_message(user_id, message.chat.id, msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("choose_delxp_"))
def choose_skill_for_delete(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    skill_id = int(call.data.split("_")[2])
    user_states[user_id] = {"deleting_xp": True, "skill_id": skill_id}

    prompt = bot.send_message(call.message.chat.id, "Напиши насколько XP нужно уменьшить навык.")
    remember_skill_prompt(user_id, call.message.chat.id, prompt.message_id)

    bot.answer_callback_query(call.id)

# ================= РЕЙТИНГ =================

@bot.message_handler(commands=['rating'])
def rating(message):
    cursor.execute("""
        SELECT users.username,
        users.saved_xp + IFNULL(SUM(skills.xp),0) as total_xp
        FROM users
        LEFT JOIN skills ON skills.user_id = users.user_id
        GROUP BY users.user_id
        ORDER BY total_xp DESC
        LIMIT 10
    """)
    top = cursor.fetchall()

    if not top:
        bot.send_message(message.chat.id, "Пока нет данных.")
        return

    text = "Рейтинг по общему XP.\n\n"
    for i, user in enumerate(top, 1):
        name = f"@{user[0]}" if user[0] else "Без_ника"
        text += f"{i}. {name} — {user[1]} XP\n"

    bot.send_message(message.chat.id, text)

# ================= RENAME =================

@bot.callback_query_handler(func=lambda call: call.data == "rename_cancel")
def cancel_rename(call):
    delete_last_message(call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("rename_"))
def rename_selected(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    skill_id = int(call.data.split("_")[1])

    user_states[user_id] = {
        "renaming": True,
        "skill_id": skill_id
    }

    prompt = bot.send_message(call.message.chat.id, "Укажи новое имя навыка.")
    remember_skill_prompt(user_id, call.message.chat.id, prompt.message_id)

    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['rename'])
def rename_skill(message):
    user_id = message.from_user.id

    if not has_skills(user_id):
        bot.send_message(message.chat.id, "Сначала нажми первую команду /start.")
        return

    cursor.execute("SELECT id, name FROM skills WHERE user_id=%s", (user_id,))
    skills = cursor.fetchall()

    markup = InlineKeyboardMarkup()
    for skill in skills:
        markup.add(InlineKeyboardButton(skill[1], callback_data=f"rename_{skill[0]}"))

    markup.add(InlineKeyboardButton("Я передумал.", callback_data="rename_cancel"))

    msg = bot.send_message(
        message.chat.id,
        "Какому навыку нужно изменить имя?🤔",
        reply_markup=markup
    )

    remember_message(user_id, message.chat.id, msg.message_id)

    
    

# ================= DELADD =================

@bot.message_handler(commands=['deladdskills'])
def deladdskills(message):
    user_id = message.from_user.id

    if not has_skills(user_id):
        bot.send_message(message.chat.id, "Сначала нажми первую команду /start.")
        return

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("Удалить", callback_data="delete_mode"),
        InlineKeyboardButton("Добавить", callback_data="add_mode")
    )

    msg = bot.send_message(
        message.chat.id,
        "Ты хочешь удалить или добавить новый навык?",
        reply_markup=markup
    )

    remember_message(user_id, message.chat.id, msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "delete_mode")
def delete_mode(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    cursor.execute("SELECT id, name FROM skills WHERE user_id=%s", (user_id,))
    skills = cursor.fetchall()

    markup = InlineKeyboardMarkup()
    for skill in skills:
        markup.add(InlineKeyboardButton(skill[1], callback_data=f"delete_{skill[0]}"))

    msg = bot.send_message(
        call.message.chat.id,
        "Выбери навык, чтобы его удалить.😔\n\nОпыт, который ты получил ранее, будет сохранен.",
        reply_markup=markup
    )

    remember_message(user_id, call.message.chat.id, msg.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_"))
def confirm_delete(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    skill_id = int(call.data.split("_")[1])

    cursor.execute("SELECT xp FROM skills WHERE id=%s", (skill_id,))
    xp_value = cursor.fetchone()[0]

    cursor.execute("UPDATE users SET saved_xp = saved_xp + %s WHERE user_id=%s",
                   (xp_value, user_id))

    cursor.execute("DELETE FROM skills WHERE id=%s", (skill_id,))
    

    bot.send_message(call.message.chat.id, "Твой навык удален.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "add_mode")
def add_mode(call):
    user_id = call.from_user.id
    delete_last_message(user_id)

    user_states[user_id] = {"adding": True}

    prompt = bot.send_message(call.message.chat.id, "Дай название новому навыку.")
    remember_skill_prompt(user_id, call.message.chat.id, prompt.message_id)

    bot.answer_callback_query(call.id)

# ================= ЗАПУСК =================


bot.infinity_polling()
