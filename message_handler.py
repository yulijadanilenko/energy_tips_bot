"""Пилот тренажёра «Свет без лишних затрат», 5–7 классы.

Совместим с прежним main.py. /start или /train — личная тренировка.
Рассылки прежнего main.py намеренно отключены на время пилота.
Прогресс хранится в памяти до перезапуска. Ответы, если доступно,
пишутся в существующий лист Responses; старые строки не изменяются.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import gspread
import telebot
from google.oauth2.service_account import Credentials
from telebot import types


QUESTIONS = [
    {
        "key": "light_entry",
        "stage": "Входное задание",
        "text": "В классе солнечно. Свет у окон выключили, но у доски стало трудно читать записи. Как поступить?",
        "options": ["Выключить весь свет ради экономии", "Включить свет там, где его недостаточно", "Оставить всё как есть"],
        "correct": 1,
        "explanation": "Экономия не должна мешать хорошо видеть. Нужное освещение сохраняем, лишнее выключаем. В классе решение об освещении принимаем вместе с учителем.",
    },
    {
        "key": "light_practice_1",
        "stage": "Тренировка · 1 из 2",
        "text": "Ты читаешь за столом. За окном стемнело, буквы стало плохо видно. Как поступить?",
        "options": ["Продолжать читать в полумраке", "Включить весь свет в квартире", "Включить подходящий свет у рабочего места"],
        "correct": 2,
        "hint": "Нужен свет там, где ты читаешь. Подумай, какой вариант обеспечивает видимость без лишнего освещения.",
        "explanation": "Освещаем рабочее место так, чтобы текст был хорошо виден. Если настольной лампы недостаточно, добавляем общее освещение.",
    },
    {
        "key": "light_practice_2",
        "stage": "Тренировка · 2 из 2",
        "text": "Ты уходишь из комнаты надолго. Свет включён, но младшая сестра остаётся собирать пазл. Что сделаешь?",
        "options": ["Оставлю необходимый ей свет", "Выключу свет: я ведь ухожу", "Попрошу собирать пазл в темноте"],
        "correct": 0,
        "hint": "В комнате остаётся человек, которому нужно хорошо видеть. Учти не только свои действия, но и его занятие.",
        "explanation": "Перед выключением света проверяем, нужен ли он другим. Экономим только там, где освещение действительно лишнее.",
    },
    {
        "key": "light_final",
        "stage": "Итоговое задание",
        "text": "Дома включён свет в пустой комнате и над столом, где брат рисует. В пустую комнату пока никто не собирается. Как разумнее поступить?",
        "options": ["Выключить свет в обеих комнатах", "Выключить в пустой, сохранить нужный свет у стола", "Оставить весь свет до вечера"],
        "correct": 1,
        "explanation": "Свет в пустой комнате можно выключить, а необходимое освещение рабочего места сохранить. Это экономия с учётом потребностей людей.",
    },
]


class MessageHandler:
    def __init__(self, token, config):
        # Последовательная обработка исключает одновременный учёт двух нажатий.
        self.bot = telebot.TeleBot(token, threaded=False)
        self.config = config
        self.logger = logging.getLogger("telegram_bot")
        self.sessions = {}
        self.sheet = None
        self._init_sheet()
        self._register_handlers()

    def _init_sheet(self):
        raw = os.getenv("GOOGLE_CREDENTIALS", "").strip()
        spreadsheet_id = os.getenv("SPREADSHEET_ID", "").strip()
        if not raw or not spreadsheet_id:
            self.logger.warning("Google Sheets не настроены; пилот работает без записи ответов.")
            return
        try:
            credentials = Credentials.from_service_account_info(
                json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            book = gspread.authorize(credentials).open_by_key(spreadsheet_id)
            try:
                self.sheet = book.worksheet("Responses")
            except gspread.exceptions.WorksheetNotFound:
                self.sheet = book.add_worksheet(title="Responses", rows=1000, cols=20)
                self.sheet.append_row(
                    ["timestamp", "chat_title", "chat_id", "user", "user_id",
                     "question_key", "answer_value", "message_id"],
                    value_input_option="RAW",
                )
        except Exception as exc:
            self.sheet = None
            self.logger.warning("Google Sheets недоступны (%s).", type(exc).__name__)

    def _save(self, call, session, key, payload):
        if self.sheet is None:
            session["saved"] = False
            return
        payload = dict(payload, session_id=session["id"], module="light_v1")
        try:
            self.sheet.append_row([
                datetime.now(timezone.utc).isoformat(), "Личная тренировка",
                call.message.chat.id, "", call.from_user.id, key,
                json.dumps(payload, ensure_ascii=False), call.message.message_id,
            ], value_input_option="RAW")
        except Exception as exc:
            session["saved"] = False
            self.logger.warning("Ответ не записан в Sheets (%s).", type(exc).__name__)

    def _keyboard(self, session, actions):
        kb = types.InlineKeyboardMarkup(row_width=1)
        for label, action, value in actions:
            kb.add(types.InlineKeyboardButton(
                label, callback_data=f"t:{session['id']}:{session['rev']}:{action}:{value}"
            ))
        return kb

    def _show(self, chat_id, session):
        phase = session["phase"]
        if phase == "welcome":
            text = (
                "💡 Свет без лишних затрат\nТренажёр для 5–7 классов · около 5 минут\n\n"
                "Ты потренируешься решать, где свет нужен, а где его можно выключить. "
                "Сначала — задача без подсказки, затем объяснение, тренировка и итог.\n\n"
                "Если подключена таблица, бот записывает твой Telegram ID и ответы для анализа. "
                "Имя не записывается. В этом пилоте после перезапуска бота придётся начать заново."
            )
            actions = [("Начать", "begin", "0")]
        elif phase == "question":
            question = QUESTIONS[session["index"]]
            text = f"{question['stage']}\n\n{question['text']}"
            if session.get("hint"):
                text += "\n\nПопробуй ещё раз. " + question["hint"]
            actions = [(label, "answer", str(i)) for i, label in enumerate(question["options"])]
        elif phase == "feedback":
            text = session["feedback"]
            actions = [("Дальше", "next", "0")]
        elif phase == "observation":
            text = (
                "🔎 Наблюдение в жизни\n\nОсмотрись там, где ты сейчас находишься. "
                "Есть ли включённый свет, который никому не нужен?\n\n"
                "Пока только наблюдай. В школе обсуди вывод с учителем, дома — со взрослым. "
                "Не трогай лампы, провода и розетки.\n\n"
                "Здесь нет правильного ответа — отметь, что удалось заметить."
            )
            actions = [("Да, заметил(а) лишний свет", "observe", "extra"),
                       ("Нет, включённый свет нужен", "observe", "needed"),
                       ("Сейчас не могу проверить", "observe", "skip")]
        else:
            entry = "верно" if session["first"][0] else "пока неверно"
            final = "верно" if session["first"][3] else "пока неверно"
            practice = sum(bool(session["first"][i]) for i in (1, 2))
            text = (
                f"🏁 Модуль завершён!\n\nВходное задание: {entry}.\n"
                f"Тренировочные задачи с первой попытки: {practice} из 2.\n"
                f"Итоговое задание: {final}.\n\n"
                "Главное правило: сохраняем свет там, где он нужен людям, и выключаем лишний.\n"
                "Это результат короткой тренировки, а не школьная отметка."
            )
            if not session["first"][3]:
                text += "\nСтоит ещё раз разобрать итоговую ситуацию и повторить модуль."
            if session["saved"]:
                text += "\n\nОтветы записаны в таблицу."
            else:
                text += "\n\nНе все ответы удалось записать в таблицу. Результат показан здесь."
            actions = []
        kb = self._keyboard(session, actions) if actions else None
        if session.get("message_id"):
            self.bot.edit_message_text(text, chat_id=chat_id,
                                       message_id=session["message_id"], reply_markup=kb)
        else:
            sent = self.bot.send_message(chat_id, text, reply_markup=kb)
            session["message_id"] = sent.message_id

    def _begin_session(self, message):
        if message.chat.type != "private":
            self.bot.reply_to(message, "Открой личный чат с ботом и отправь /train. У каждого будет своя тренировка.")
            return
        session = {
            "id": uuid.uuid4().hex[:12], "rev": 0, "phase": "welcome", "index": 0,
            "attempts": [0] * 4, "first": [None] * 4, "saved": self.sheet is not None,
            "message_id": None, "hint": False,
        }
        self.sessions[message.from_user.id] = session
        self._show(message.chat.id, session)

    def _advance(self, session, action, value):
        """Проверяет действие и меняет состояние. Возвращает событие для записи."""
        phase = session["phase"]
        event = None
        if phase == "welcome" and action == "begin" and value == "0":
            session["phase"] = "question"
        elif phase == "question" and action == "answer":
            question = QUESTIONS[session["index"]]
            if value not in {str(i) for i in range(len(question["options"]))}:
                raise ValueError("Недопустимый ответ")
            choice = int(value)
            index = session["index"]
            session["attempts"][index] += 1
            correct = choice == question["correct"]
            if session["first"][index] is None:
                session["first"][index] = correct
            event = (question["key"], {
                "choice": choice, "label": question["options"][choice],
                "correct": correct, "attempt": session["attempts"][index],
                "stage": question["stage"],
            })
            if index in (1, 2) and not correct:
                session["hint"] = True
            else:
                session["hint"] = False
                session["phase"] = "feedback"
                prefix = "Верно! " if correct else "Разберём решение. "
                session["feedback"] = prefix + question["explanation"]
        elif phase == "feedback" and action == "next" and value == "0":
            if session["index"] == 2:
                session["phase"] = "observation"
            elif session["index"] == 3:
                session["phase"] = "done"
                event = ("light_summary", {"first_correct": session["first"],
                                            "attempts": session["attempts"]})
            else:
                session["index"] += 1
                session["phase"] = "question"
        elif phase == "observation" and action == "observe" and value in {"extra", "needed", "skip"}:
            event = ("light_observation", {"observation": value})
            session["index"] = 3
            session["phase"] = "question"
        else:
            raise ValueError("Действие не соответствует этапу")
        session["rev"] += 1
        return event

    def _on_callback(self, call):
        if not call.message or call.message.chat.type != "private":
            self.bot.answer_callback_query(call.id, "Тренировка доступна в личном чате: /train")
            return
        session = self.sessions.get(call.from_user.id)
        try:
            prefix, session_id, revision, action, value = call.data.split(":")
            valid = (prefix == "t" and session is not None
                     and session_id == session["id"] and revision == str(session["rev"])
                     and call.message.message_id == session["message_id"])
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            self.bot.answer_callback_query(call.id, "Кнопка устарела. Продолжить: /continue. Начать заново: /train")
            return
        try:
            event = self._advance(session, action, value)
        except ValueError:
            self.bot.answer_callback_query(call.id, "Этот вариант недоступен.")
            return
        # Подтверждаем получение, а не сохранение: запись в Sheets ещё впереди.
        try:
            self.bot.answer_callback_query(call.id, "Ответ принят")
        except Exception:
            pass
        if event:
            self._save(call, session, *event)
        try:
            self._show(call.message.chat.id, session)
        except Exception as exc:
            self.logger.warning("Не удалось обновить карточку (%s).", type(exc).__name__)
            self.bot.send_message(call.message.chat.id, "Не удалось обновить карточку. Отправь /continue.")

    def _register_handlers(self):
        self.bot.message_handler(commands=["start", "train"])(self._begin_session)
        self.bot.callback_query_handler(func=lambda c: bool(c.data and c.data.startswith("t:")))(self._on_callback)

        @self.bot.message_handler(commands=["continue"])
        def resume(message):
            if message.chat.type != "private":
                self.bot.reply_to(message, "Продолжай тренировку в личном чате с ботом.")
                return
            session = self.sessions.get(message.from_user.id)
            if session is None:
                self._begin_session(message)
            else:
                # Новая карточка; кнопки старой отклоняются по message_id.
                session["message_id"] = None
                self._show(message.chat.id, session)

        @self.bot.callback_query_handler(func=lambda c: bool(c.data and c.data.startswith("answer:")))
        def old_button(call):
            self.bot.answer_callback_query(call.id, "Это прежняя рассылка. Для тренажёра отправь /train в личном чате.")

    def send_daily_message(self):
        self.logger.info("Пилот: автоматическая рассылка отключена. Запуск вручную: /train.")

    def send_watch_message(self):
        self.logger.info("Пилот: автоматическая рассылка видео отключена.")

    def start(self):
        self.bot.remove_webhook()
        self.logger.info("Пилот тренажёра запущен. Команды: /start, /train, /continue.")
        self.bot.infinity_polling(timeout=60, long_polling_timeout=50)
