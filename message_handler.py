"""Тренажёр «Свет без лишних затрат» для 5–7 классов.

Команды:
    /start — вход, в том числе по ссылке класса
    /train — новая тренировка с выбором класса
    /continue — продолжение текущей тренировки

Прогресс хранится в памяти до перезапуска бота.
Ответы записываются в лист Responses.
Старые строки таблицы не изменяются.
Автоматические рассылки отключены.
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


CLASSES = {
    "5a": "5А",
    "5b": "5Б",
    "5v": "5В",
    "6a": "6А",
    "6b": "6Б",
    "6v": "6В",
    "7a": "7А",
    "7b": "7Б",
    "7v": "7В",
    "test": "Тестирование",
}


QUESTIONS = [
    {
        "key": "light_entry",
        "stage": "Входное задание",
        "text": (
            "В классе солнечно. Свет у окон выключили, но у доски "
            "стало трудно читать записи. Как поступить?"
        ),
        "options": [
            "Выключить весь свет ради экономии",
            "Включить свет там, где его недостаточно",
            "Оставить всё как есть",
        ],
        "correct": 1,
        "explanation": (
            "Экономия не должна мешать хорошо видеть. Нужное освещение "
            "сохраняем, лишнее выключаем. В классе решение об освещении "
            "принимаем вместе с учителем."
        ),
    },
    {
        "key": "light_practice_1",
        "stage": "Тренировка · 1 из 2",
        "text": (
            "Ты читаешь за столом. За окном стемнело, буквы стало "
            "плохо видно. Как поступить?"
        ),
        "options": [
            "Продолжать читать в полумраке",
            "Включить весь свет в квартире",
            "Включить подходящий свет у рабочего места",
        ],
        "correct": 2,
        "hint": (
            "Нужен свет там, где ты читаешь. Подумай, какой вариант "
            "обеспечивает видимость без лишнего освещения."
        ),
        "explanation": (
            "Освещаем рабочее место так, чтобы текст был хорошо виден. "
            "Если настольной лампы недостаточно, добавляем общее освещение."
        ),
    },
    {
        "key": "light_practice_2",
        "stage": "Тренировка · 2 из 2",
        "text": (
            "Ты уходишь из комнаты надолго. Свет включён, но младшая "
            "сестра остаётся собирать пазл. Что сделаешь?"
        ),
        "options": [
            "Оставлю необходимый ей свет",
            "Выключу свет: я ведь ухожу",
            "Попрошу собирать пазл в темноте",
        ],
        "correct": 0,
        "hint": (
            "В комнате остаётся человек, которому нужно хорошо видеть. "
            "Учти не только свои действия, но и его занятие."
        ),
        "explanation": (
            "Перед выключением света проверяем, нужен ли он другим. "
            "Экономим только там, где освещение действительно лишнее."
        ),
    },
    {
        "key": "light_final",
        "stage": "Итоговое задание",
        "text": (
            "Дома включён свет в пустой комнате и над столом, где брат "
            "рисует. В пустую комнату пока никто не собирается. "
            "Как разумнее поступить?"
        ),
        "options": [
            "Выключить свет в обеих комнатах",
            "Выключить в пустой, сохранить нужный свет у стола",
            "Оставить весь свет до вечера",
        ],
        "correct": 1,
        "explanation": (
            "Свет в пустой комнате можно выключить, а необходимое "
            "освещение рабочего места сохранить. Это экономия "
            "с учётом потребностей людей."
        ),
    },
]


class MessageHandler:
    def __init__(self, token, config):
        # Обрабатываем нажатия последовательно.
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
            self.logger.warning(
                "Google Sheets не настроены; бот работает без записи ответов."
            )
            return

        try:
            credentials = Credentials.from_service_account_info(
                json.loads(raw),
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
            book = gspread.authorize(credentials).open_by_key(spreadsheet_id)

            try:
                self.sheet = book.worksheet("Responses")
            except gspread.exceptions.WorksheetNotFound:
                self.sheet = book.add_worksheet(
                    title="Responses",
                    rows=1000,
                    cols=20,
                )
                self.sheet.append_row(
                    [
                        "timestamp",
                        "chat_title",
                        "chat_id",
                        "user",
                        "user_id",
                        "question_key",
                        "answer_value",
                        "message_id",
                    ],
                    value_input_option="RAW",
                )

        except Exception as exc:
            self.sheet = None
            self.logger.warning(
                "Google Sheets недоступны (%s).",
                type(exc).__name__,
            )

    def _save(self, call, session, key, payload):
        if self.sheet is None:
            session["saved"] = False
            return

        class_code = session.get("class_code")
        if class_code not in CLASSES:
            session["saved"] = False
            self.logger.warning("Ответ не записан: класс не выбран.")
            return

        payload = dict(
            payload,
            session_id=session["id"],
            module="light_v1",
            class_code=class_code,
            class_name=CLASSES[class_code],
            is_test=class_code == "test",
        )

        try:
            self.sheet.append_row(
                [
                    datetime.now(timezone.utc).isoformat(),
                    CLASSES[class_code],
                    call.message.chat.id,
                    "",
                    call.from_user.id,
                    key,
                    json.dumps(payload, ensure_ascii=False),
                    call.message.message_id,
                ],
                value_input_option="RAW",
            )

        except Exception as exc:
            session["saved"] = False
            self.logger.warning(
                "Ответ не записан в Sheets (%s).",
                type(exc).__name__,
            )

    def _keyboard(self, session, actions):
        keyboard = types.InlineKeyboardMarkup(row_width=1)

        for label, action, value in actions:
            keyboard.add(
                types.InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"t:{session['id']}:{session['rev']}:{action}:{value}"
                    ),
                )
            )

        return keyboard

    def _show(self, chat_id, session):
        phase = session["phase"]

        if phase == "choose_class":
            text = (
                "Привет! Перед тренировкой выбери свой класс.\n\n"
                "Если ты проверяешь работу бота, выбери «Тестирование»."
            )
            actions = [
                (label, "class", code)
                for code, label in CLASSES.items()
            ]

        elif phase == "confirm_class":
            label = CLASSES[session["class_code"]]
            text = (
                f"Выбрано: {label}.\n\n"
                "Всё верно? С этой отметкой будут сохранены твои ответы."
            )
            actions = [
                ("Да, всё верно", "confirm", "yes"),
                ("Выбрать другой класс", "confirm", "no"),
            ]

        elif phase == "welcome":
            label = CLASSES[session["class_code"]]
            text = (
                "💡 Свет без лишних затрат\n"
                "Тренажёр для 5–7 классов · около 5 минут\n"
                f"Участие: {label}\n\n"
                "Ты потренируешься решать, где свет нужен, а где его можно "
                "выключить. Сначала — задача без подсказки, затем "
                "объяснение, тренировка и итог.\n\n"
                "Если подключена таблица, бот записывает твой Telegram ID, "
                "выбранный класс и ответы для анализа. Имя не записывается.\n\n"
                "Чтобы продолжить прерванную тренировку, отправь /continue. "
                "В этом пилоте после перезапуска бота придётся начать заново."
            )
            actions = [("Начать", "begin", "0")]

        elif phase == "question":
            question = QUESTIONS[session["index"]]
            text = f"{question['stage']}\n\n{question['text']}"

            if session.get("hint"):
                text += "\n\nПопробуй ещё раз. " + question["hint"]

            actions = [
                (label, "answer", str(index))
                for index, label in enumerate(question["options"])
            ]

        elif phase == "feedback":
            text = session["feedback"]
            actions = [("Дальше", "next", "0")]

        elif phase == "observation":
            text = (
                "🔎 Наблюдение в жизни\n\n"
                "Осмотрись там, где ты сейчас находишься. "
                "Есть ли включённый свет, который никому не нужен?\n\n"
                "Пока только наблюдай. В школе обсуди вывод с учителем, "
                "дома — со взрослым. Не трогай лампы, провода и розетки.\n\n"
                "Здесь нет правильного ответа — отметь, что удалось заметить."
            )
            actions = [
                ("Да, заметил(а) лишний свет", "observe", "extra"),
                ("Нет, включённый свет нужен", "observe", "needed"),
                ("Сейчас не могу проверить", "observe", "skip"),
            ]

        elif phase == "done":
            entry = "верно" if session["first"][0] else "пока неверно"
            final = "верно" if session["first"][3] else "пока неверно"
            practice = sum(
                bool(session["first"][index]) for index in (1, 2)
            )
            label = CLASSES[session["class_code"]]

            text = (
                f"🏁 Модуль завершён!\nУчастие: {label}\n\n"
                f"Входное задание: {entry}.\n"
                f"Тренировочные задачи с первой попытки: {practice} из 2.\n"
                f"Итоговое задание: {final}.\n\n"
                "Главное правило: сохраняем свет там, где он нужен людям, "
                "и выключаем лишний.\n"
                "Это результат короткой тренировки, а не школьная отметка."
            )

            if not session["first"][3]:
                text += (
                    "\nСтоит ещё раз разобрать итоговую ситуацию "
                    "и повторить модуль."
                )

            if session["saved"]:
                text += "\n\nОтветы записаны в таблицу."
            else:
                text += (
                    "\n\nНе все ответы удалось записать в таблицу. "
                    "Результат показан здесь."
                )

            actions = []

        else:
            raise ValueError("Неизвестный этап тренировки")

        keyboard = self._keyboard(session, actions) if actions else None

        if session.get("message_id"):
            self.bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=session["message_id"],
                reply_markup=keyboard,
            )
        else:
            sent = self.bot.send_message(
                chat_id,
                text,
                reply_markup=keyboard,
            )
            session["message_id"] = sent.message_id

    def _begin_session(self, message):
        if message.chat.type != "private":
            self.bot.reply_to(
                message,
                "Открой личный чат с ботом и отправь /train. "
                "У каждого будет своя тренировка.",
            )
            return

        parts = (message.text or "").split(maxsplit=1)
        command = parts[0].split("@")[0] if parts else ""
        class_code = None

        # Ссылки вида https://t.me/EnergoGymBot?start=5a
        if command == "/start" and len(parts) == 2:
            candidate = parts[1].strip()
            if candidate in CLASSES:
                class_code = candidate

        session = {
            "id": uuid.uuid4().hex[:12],
            "rev": 0,
            "phase": "confirm_class" if class_code else "choose_class",
            "class_code": class_code,
            "index": 0,
            "attempts": [0] * len(QUESTIONS),
            "first": [None] * len(QUESTIONS),
            "saved": self.sheet is not None,
            "message_id": None,
            "hint": False,
        }

        self.sessions[message.from_user.id] = session
        self._show(message.chat.id, session)

    def _advance(self, session, action, value):
        """Проверяет действие, меняет этап и возвращает запись для таблицы."""
        phase = session["phase"]
        event = None

        if phase == "choose_class" and action == "class":
            if value not in CLASSES:
                raise ValueError("Неизвестный класс")

            session["class_code"] = value
            session["phase"] = "confirm_class"

        elif phase == "confirm_class" and action == "confirm":
            if value == "yes" and session["class_code"] in CLASSES:
                session["phase"] = "welcome"
            elif value == "no":
                session["class_code"] = None
                session["phase"] = "choose_class"
            else:
                raise ValueError("Недопустимое подтверждение")

        elif phase == "welcome" and action == "begin" and value == "0":
            if session["class_code"] not in CLASSES:
                raise ValueError("Класс не выбран")

            session["phase"] = "question"

        elif phase == "question" and action == "answer":
            index = session["index"]
            question = QUESTIONS[index]

            valid_values = {
                str(i) for i in range(len(question["options"]))
            }
            if value not in valid_values:
                raise ValueError("Недопустимый ответ")

            choice = int(value)
            session["attempts"][index] += 1
            correct = choice == question["correct"]

            if session["first"][index] is None:
                session["first"][index] = correct

            event = (
                question["key"],
                {
                    "choice": choice,
                    "label": question["options"][choice],
                    "correct": correct,
                    "attempt": session["attempts"][index],
                    "stage": question["stage"],
                },
            )

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
                event = (
                    "light_summary",
                    {
                        "first_correct": session["first"],
                        "attempts": session["attempts"],
                    },
                )

            else:
                session["index"] += 1
                session["phase"] = "question"

        elif (
            phase == "observation"
            and action == "observe"
            and value in {"extra", "needed", "skip"}
        ):
            event = ("light_observation", {"observation": value})
            session["index"] = 3
            session["phase"] = "question"

        else:
            raise ValueError("Действие не соответствует этапу")

        session["rev"] += 1
        return event

    def _on_callback(self, call):
        if not call.message or call.message.chat.type != "private":
            self.bot.answer_callback_query(
                call.id,
                "Тренировка доступна в личном чате: /train",
            )
            return

        session = self.sessions.get(call.from_user.id)

        try:
            prefix, session_id, revision, action, value = call.data.split(":")
            valid = (
                prefix == "t"
                and session is not None
                and session_id == session["id"]
                and revision == str(session["rev"])
                and call.message.message_id == session["message_id"]
            )
        except (ValueError, AttributeError):
            valid = False

        if not valid:
            self.bot.answer_callback_query(
                call.id,
                "Кнопка устарела. Продолжить: /continue. "
                "Начать заново: /train",
            )
            return

        try:
            event = self._advance(session, action, value)
        except ValueError:
            self.bot.answer_callback_query(
                call.id,
                "Этот вариант недоступен.",
            )
            return

        # Подтверждаем нажатие, а не успешную запись в таблицу.
        try:
            self.bot.answer_callback_query(call.id, "Принято")
        except Exception:
            pass

        if event:
            self._save(call, session, *event)

        try:
            self._show(call.message.chat.id, session)
        except Exception as exc:
            self.logger.warning(
                "Не удалось обновить карточку (%s).",
                type(exc).__name__,
            )
            self.bot.send_message(
                call.message.chat.id,
                "Не удалось обновить карточку. Отправь /continue.",
            )

    def _register_handlers(self):
        self.bot.message_handler(
            commands=["start", "train"]
        )(self._begin_session)

        self.bot.callback_query_handler(
            func=lambda call: bool(
                call.data and call.data.startswith("t:")
            )
        )(self._on_callback)

        @self.bot.message_handler(commands=["continue"])
        def resume(message):
            if message.chat.type != "private":
                self.bot.reply_to(
                    message,
                    "Продолжай тренировку в личном чате с ботом.",
                )
                return

            session = self.sessions.get(message.from_user.id)

            if session is None:
                self._begin_session(message)
            else:
                # Старые кнопки отклоняются по номеру сообщения.
                session["message_id"] = None
                self._show(message.chat.id, session)

        @self.bot.callback_query_handler(
            func=lambda call: bool(
                call.data and call.data.startswith("answer:")
            )
        )
        def old_button(call):
            self.bot.answer_callback_query(
                call.id,
                "Это прежняя рассылка. Для тренажёра "
                "отправь /train в личном чате.",
            )

    def send_daily_message(self):
        self.logger.info(
            "Автоматическая рассылка отключена. Запуск вручную: /train."
        )

    def send_watch_message(self):
        self.logger.info("Автоматическая рассылка видео отключена.")

    def start(self):
        self.bot.remove_webhook()
        self.logger.info(
            "Тренажёр запущен. Команды: /start, /train, /continue."
        )
        self.bot.infinity_polling(
            timeout=60,
            long_polling_timeout=50,
        )