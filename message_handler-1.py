"""Курс v2. Один экземпляр процесса. Google Sheets — постоянный журнал.
Каждое изменение и ответ сохраняются одной операцией до следующего экрана.
"""
import copy
import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import gspread
import telebot
from google.oauth2.service_account import Credentials
from telebot import types
from course_data import CLASSES, MODULES, PRE, POST, TOPICS, INTENT

DAY = 86400
VERSION = 'energy_v2_20261021'
ORDER = ['pre', 'm0', 'r0', 'm1', 'r1', 'm2', 'r2', 'm3', 'r3', 'post']
HEADERS = ['timestamp_utc','class','user_id','event','activity','question',
           'choice','correct','attempt','is_test','session_id','event_id',
           'details_json','state_json']

def now():
    return time.time()

def questions(activity):
    if activity == 'pre':
        return PRE
    if activity == 'post':
        return POST
    m = MODULES[int(activity[1:])]
    return [m['repeat']] if activity.startswith('r') else m['questions']

def title(activity):
    if activity == 'pre':
        return 'Стартовая проверка'
    if activity == 'post':
        return 'Итоговая проверка'
    return ('Повторение: ' if activity.startswith('r') else '') + MODULES[int(activity[1:])]['title']

def next_activity(s):
    return next((a for a in ORDER if a not in s['results']), None)

def available_at(s, activity):
    if s['class_code'] == 'test' or activity in (None, 'pre', 'm0'):
        return 0
    if activity.startswith('m'):
        return s['enrolled_at'] + int(activity[1:]) * 7 * DAY
    if activity.startswith('r'):
        return s['results']['m' + activity[1:]]['finished_at'] + 3 * DAY
    return s['results']['r3']['finished_at']

def invitation_due(s, local):
    if not 16 <= local.hour < 19 or not s['enrolled_at'] or not s['notifications'] or s['class_code']=='test':
        return None
    a = next_activity(s)
    if not a or now() < available_at(s,a):
        return None
    if s['active'] and s['active']['id']==a and now() < s.get('last_interaction', s['enrolled_at']) + 3*DAY:
        return None
    week = local.strftime('%G-W%V')
    if a in s['notices'] or sum(v==week for v in s['notices'].values()) >= 2:
        return None
    return a

def new_state(code=None):
    return dict(version=VERSION, id=uuid.uuid4().hex[:12], rev=0,
                screen='intro', class_code=code, enrolled_at=None,
                notifications=False, notices={}, results={}, active=None,
                support=None, intent=None)

def complete(s):
    a = s['active']
    s['results'][a['id']] = dict(first=a['first'], attempts=a['attempts'],
        choices=a['choices'], finished_at=now(), observation=a.get('observation'),
        support=a.get('support'))
    s['screen'] = 'result'


def advance(s, action, value):
    """Чистый переход: вызывающий сохраняет копию, затем публикует её."""
    event = {'event': action, 'activity': s['active']['id'] if s['active'] else ''}
    screen = s['screen']
    if action == 'menu' and s['enrolled_at']:
        s['screen'] = 'menu'
    elif action == 'intro' and screen == 'intro':
        s['screen'] = 'confirm' if s['class_code'] else 'class'
    elif action == 'class' and screen == 'class' and value in CLASSES:
        s['class_code'], s['screen'] = value, 'confirm'
    elif action == 'confirm' and screen == 'confirm' and value in ('yes','no'):
        if value == 'no':
            s['class_code'], s['screen'] = None, 'class'
        else:
            s['screen'] = 'notice_choice'
    elif action == 'notify' and screen in ('notice_choice','settings') and value in ('on','off'):
        s['notifications'] = value == 'on'
        if s['enrolled_at'] is None:
            s['enrolled_at'] = now()
            event['event'] = 'enrollment'
        s['screen'] = 'menu'
    elif action in ('progress','settings') and screen == 'menu':
        s['screen'] = action
    elif action == 'change_class' and screen == 'settings' and not s['active'] and not s['results']:
        s['enrolled_at'] = None
        s['screen'] = 'class'
    elif action == 'open' and screen == 'menu':
        a = s['active']
        if a and a['id'] not in s['results']:
            s['screen'] = a['phase']
        else:
            aid = next_activity(s)
            if not aid or now() < available_at(s, aid):
                raise ValueError('Этап пока недоступен')
            n = len(questions(aid))
            s['active'] = dict(id=aid, index=0, first=[None]*n,
                attempts=[0]*n, choices=[None]*n, phase='support', hint=False)
            s['screen'] = 'support'
            event.update(event='activity_started', activity=aid)
    elif action == 'support' and screen == 'support' and value in ('alone','teacher','family'):
        s['active']['support'] = value
        s['screen'] = 'welcome'
    elif action == 'begin' and screen == 'welcome':
        s['screen'] = 'question'
    elif action == 'answer' and screen == 'question':
        a = s['active']
        i = a['index']
        question = questions(a['id'])[i]
        if value not in [str(j) for j in range(len(question['options']))]:
            raise ValueError('Недопустимый ответ')
        choice = int(value)
        correct = choice == question['correct']
        a['attempts'][i] += 1
        if a['first'][i] is None:
            a['first'][i], a['choices'][i] = correct, choice
        event.update(event='answer', question=i, choice=choice, correct=correct,
                     attempt=a['attempts'][i], label=question['options'][choice])
        if a['id'] in ('pre','post'):
            if i + 1 == len(questions(a['id'])):
                complete(s)
                event['completed'] = True
            else:
                a['index'] += 1
        elif a['id'].startswith('m') and i in (1,2) and not correct:
            a['hint'] = True
            a['last_choice'] = choice
            s['screen'] = 'retry'
        else:
            a['hint'] = False
            a['feedback'] = ('Верно! ' if correct else 'Давай разберёмся. ') + question['explanation']
            s['screen'] = 'feedback'
    elif action == 'retry' and screen == 'retry':
        s['active']['hint'] = False
        s['screen'] = 'question'
    elif action == 'next' and screen == 'feedback':
        a = s['active']
        if a['id'].startswith('r') or a['index'] == 3:
            complete(s)
            event['event'] = 'activity_completed'
        elif a['index'] == 0:
            s['screen'] = 'rule'
        elif a['index'] == 1:
            a['index'], s['screen'] = 2, 'question'
        elif a['index'] == 2:
            s['screen'] = 'observation'
    elif action == 'rule_next' and screen == 'rule':
        s['active']['index'], s['screen'] = 1, 'question'
    elif action == 'observe' and screen == 'observation':
        a = s['active']
        m = MODULES[int(a['id'][1:])]
        if value not in [str(i) for i in range(len(m['observations']))]:
            raise ValueError('Недопустимое наблюдение')
        a['observation'] = int(value)
        event.update(event='observation', choice=int(value), self_report=True)
        s['screen'] = 'observation_feedback'
    elif action == 'observe_next' and screen == 'observation_feedback':
        s['active']['index'], s['screen'] = 3, 'question'
    elif action == 'review' and screen == 'result' and s['active']['id'] == 'post':
        s['review_index'], s['screen'] = 0, 'review'
    elif action == 'review_next' and screen == 'review':
        s['review_index'] += 1
        if s['review_index'] >= len(POST):
            s['screen'] = 'result'
    elif action == 'intent_open' and screen == 'result' and s['active']['id'] == 'post':
        s['screen'] = 'intent'
    elif action == 'intent' and screen == 'intent' and value in [str(i) for i in range(len(INTENT))]:
        s['intent'], s['screen'] = int(value), 'thanks'
        event.update(event='intention', choice=int(value), self_report=True)
    elif action == 'rule_show' and screen == 'result' and s['active']['id'].startswith('m'):
        s['screen'] = 'rule_view'
    elif action == 'result' and screen == 'rule_view':
        s['screen'] = 'result'
    elif action == 'test_reset' and screen == 'settings' and s['class_code'] == 'test':
        s['screen'] = 'reset_confirm'
    elif action == 'reset_yes' and screen == 'reset_confirm' and s['class_code'] == 'test':
        fresh = new_state('test')
        s.clear()
        s.update(fresh)
        event['event'] = 'test_reset'
    else:
        raise ValueError('Действие недоступно')
    if s.get('active') and s['screen'] in ('support','welcome','question','retry','feedback','rule','observation','observation_feedback'):
        s['active']['phase'] = s['screen']
    s['last_interaction'] = now()
    s['rev'] += 1
    return event


def render(s):
    screen = s['screen']
    actions = []
    def add(label, action, value='0'):
        actions.append((label, action, value))
    if screen == 'intro':
        text = ('⚡ Тренажёр энергосбережения для 5–7 классов\n\n'
                'Четыре короткие тренировки помогут выбирать разумные действия дома и в гимназии. '
                'Сначала — стартовая проверка, затем занятия, повторы и итог. Это не школьная отметка.\n\n'
                'Сохраняются Telegram ID, выбранный класс, ответы и прогресс. Имя не записывается. '
                'Результаты доступны организатору проекта. Продолжить позже можно командой /continue.')
        add('Продолжить','intro')
    elif screen == 'class':
        text = 'Выбери класс. Взрослым для проверки бота — «Тестирование».'
        for code, name in CLASSES.items():
            add(name, 'class', code)
    elif screen == 'confirm':
        text = f"Твой выбор: {CLASSES[s['class_code']]}. Всё верно?"
        add('Да, всё верно','confirm','yes'); add('Выбрать другой','confirm','no')
    elif screen == 'notice_choice':
        text = 'Присылать приглашения на задания? Не более двух в неделю, днём. Их можно отключить в настройках. Задания доступны и без приглашений.'
        add('Да, приглашать','notify','on'); add('Без приглашений','notify','off')
    elif screen == 'menu':
        a = next_activity(s)
        text = f"⚡ Моя тренировка\nКласс: {CLASSES[s['class_code']]}\n\n"
        if a is None:
            text += 'Все этапы пройдены. Спасибо за участие!'
        elif now() < available_at(s,a):
            date = datetime.fromtimestamp(available_at(s,a),ZoneInfo('Europe/Minsk')).strftime('%d.%m в %H:%M')
            text += f'Следующий этап: {title(a)}.\nОткроется {date} (Минск).'
        else:
            text += 'Следующий этап: ' + title(a)
            add('Продолжить' if s['active'] and s['active']['id'] == a else 'Открыть задание','open')
        add('Мой прогресс','progress'); add('Настройки','settings')
    elif screen == 'settings':
        text = 'Приглашения: ' + ('включены' if s['notifications'] else 'выключены')
        add('Отключить приглашения' if s['notifications'] else 'Включить приглашения','notify','off' if s['notifications'] else 'on')
        if not s['active'] and not s['results']:
            add('Исправить класс','change_class')
        else:
            text += '\nЕсли класс выбран ошибочно, сообщи организатору. Самостоятельная смена после начала проверки закрыта.'
        if s['class_code'] == 'test':
            add('Сбросить тестовое прохождение','test_reset')
        add('В меню','menu')
    elif screen == 'reset_confirm':
        text = 'Начать новое тестовое прохождение? Старые записи сохранятся, текущий тестовый прогресс будет сброшен.'
        add('Да, новый тест','reset_yes'); add('Отмена','menu')
    elif screen == 'progress':
        text = 'Мой прогресс\n\n' + '\n'.join(('✓ ' if a in s['results'] else '○ ') + title(a) for a in ORDER)
        add('В меню','menu')
    elif screen == 'support':
        text = 'Как ты проходишь этот этап? Это не влияет на результат.'
        add('Самостоятельно','support','alone'); add('С поддержкой учителя','support','teacher'); add('С поддержкой семьи','support','family')
    elif screen == 'welcome':
        aid = s['active']['id']
        text = title(aid) + '\n\n'
        if aid in ('pre','post'):
            text += ('Восемь ситуаций — около 5–7 минут. Один ответ на каждый вопрос, без подсказок. '
                     'Выбирай самостоятельно. Можно остановиться и вернуться через /continue.\n\n')
            text += ('Это начальная точка: мы выясняем, что ты уже знаешь. Правила разберём в тренировках.' if aid == 'pre' else 'Объяснения будут доступны после всех восьми вопросов.')
        elif aid.startswith('r'):
            text += 'Одна новая ситуация по изученной теме. Попробуй ответить самостоятельно.'
        else:
            text += MODULES[int(aid[1:])]['intro']
        add('Начать','begin'); add('Позже','menu')
    elif screen == 'question':
        a = s['active']; qs = questions(a['id']); q = qs[a['index']]
        text = f"{title(a['id'])}\nВопрос {a['index']+1} из {len(qs)}\n\n{q['text']}\n\n"
        text += '\n\n'.join(f"{'АБВГ'[i]}. {opt}" for i,opt in enumerate(q['options']))
        if a['hint']:
            text += '\n\nПопробуй ещё раз. ' + q['hint']
        # Короткие кнопки не обрезают длинные ответы на телефоне.
        for i in range(len(q['options'])):
            add('АБВГ'[i], 'answer', str(i))
    elif screen == 'retry':
        a = s['active']; q = questions(a['id'])[a['index']]
        text = (f"Пока неверно. Попытка {a['attempts'][a['index']]}.\n\n"
                f"Твой ответ: {q['options'][a['last_choice']]}\n\n"
                '💡 Подсказка: ' + q['hint'] + '\n\nПрочитай подсказку и попробуй ещё раз.')
        add('Попробовать ещё раз', 'retry')
    elif screen == 'feedback':
        text = s['active']['feedback']; add('Дальше','next')
    elif screen in ('rule','rule_view'):
        text = 'Главное правило\n\n' + MODULES[int(s['active']['id'][1:])]['rule']
        add('Попробовать' if screen == 'rule' else 'К результату','rule_next' if screen == 'rule' else 'result')
    elif screen == 'observation':
        m = MODULES[int(s['active']['id'][1:])]
        text = '🔎 Наблюдение в жизни\n\n' + m['observation'] + '\n\nЗдесь нет баллов и правильного ответа.'
        for i, label in enumerate(m['observations']):
            add(label,'observe',str(i))
    elif screen == 'observation_feedback':
        text = MODULES[int(s['active']['id'][1:])]['observation_feedback']; add('К последней ситуации','observe_next')
    elif screen == 'result':
        aid = s['active']['id']; r = s['results'][aid]
        if aid == 'pre':
            text = 'Стартовая проверка завершена. Ответы сохранены. Это твоя начальная точка; объяснения будут в тренировках. Первая тренировка уже доступна.'
        elif aid == 'post':
            text = f"🏁 Проверка завершена!\nВерных ответов: {sum(r['first'])} из 8.\n\n"
            text += '\n'.join(f'{topic}: {sum(r["first"][i*2:i*2+2])} из 2' for i,topic in enumerate(TOPICS))
            text += '\n\nЭто результат решения ситуаций, а не школьная отметка. Ответы сохранены.'
            add('Разобрать ответы','review'); add('Мой вывод','intent_open')
        elif aid.startswith('r'):
            text = 'Короткое повторение завершено. Ответ сохранён.\n\n' + questions(aid)[0]['explanation']
        else:
            text = (f"🏁 Модуль завершён!\n{title(aid)}\n\n"
                    f"Входная ситуация: {'верно' if r['first'][0] else 'разобрали с объяснением'}.\n"
                    f"Тренировочные с первой попытки: {sum(r['first'][1:3])} из 2.\n"
                    f"Итоговая: {'верно' if r['first'][3] else 'стоит повторить правило'}.\n\n"
                    'Ответы сохранены. Повторение откроется через три дня после завершения модуля.')
            if s['class_code'] == 'test':
                text = text.replace('Повторение откроется через три дня после завершения модуля.',
                                    'В тестовом режиме повторение доступно сразу через меню.')
            add('Посмотреть правило','rule_show')
        add('В меню','menu')
    elif screen == 'review':
        i = s['review_index']; q = POST[i]; choice = s['results']['post']['choices'][i]
        text = (f"Разбор {i+1} из 8\n\n{q['text']}\n\nТвой ответ: {q['options'][choice]}\n"
                f"Верный ответ: {q['options'][q['correct']]}\n\n{q['explanation']}")
        add('Дальше','review_next')
    elif screen == 'intent':
        text = 'Что возьмёшь в повседневную жизнь? Выбери одно действие. Здесь нет баллов.'
        for i, label in enumerate(INTENT):
            add(label,'intent',str(i))
    elif screen == 'thanks':
        text = 'Спасибо! Попробуй вспомнить об этом решении в подходящей ситуации.'; add('В меню','menu')
    else:
        raise ValueError('Неизвестный экран')
    if s.get('enrolled_at') and screen in ('support','question','retry','feedback','rule','observation','observation_feedback'):
        add('В меню · продолжить позже', 'menu')
    return text, actions


class Journal:
    """Журнал только дописывается. Неопределённый результат сверяется по event_id."""
    def __init__(self, book):
        try:
            self.sheet = book.worksheet('Trainer_v2')
        except gspread.exceptions.WorksheetNotFound:
            self.sheet = book.add_worksheet(title='Trainer_v2', rows=5000, cols=len(HEADERS))
            self.sheet.append_row(HEADERS, value_input_option='RAW')
        self.states = {}; self.blocked = False
        self.io_lock = threading.RLock()
        self.queue = queue.Queue()
        self.restore()
        threading.Thread(target=self._writer,daemon=True).start()

    def restore(self):
        states = {}
        for row in self.sheet.get_all_values()[1:]:
            if not row or not row[0]:
                continue
            if len(row) < 14:
                raise ValueError('Неполная строка Trainer_v2')
            s = json.loads(row[13])
            if s['version'] != VERSION:
                raise ValueError('Несовместимая версия курса')
            states[int(row[2])] = s
        self.states = states; self.blocked = False

    def commit(self, uid, s, event):
        eid = f"{s['id']}:{s['rev']}"
        code = s.get('class_code')
        row = [datetime.now(timezone.utc).isoformat(), CLASSES.get(code,''), str(uid),
               event.get('event',''), event.get('activity',''), event.get('question',''),
               event.get('choice',''), event.get('correct',''), event.get('attempt',''),
               code == 'test', s['id'], eid, json.dumps(event,ensure_ascii=False),
               json.dumps(s,ensure_ascii=False)]
        request = dict(uid=uid, state=copy.deepcopy(s), row=row,
                       done=threading.Event(), error=None)
        self.queue.put(request)
        request['done'].wait()
        if request['error'] is not None:
            raise request['error']

    def _writer(self):
        # До 100 переходов одним запросом. Не чаще одного пакета за 1,3 секунды.
        # Каждый участник ждёт подтверждения записи, прежде чем увидеть следующий шаг.
        while True:
            first = self.queue.get()
            time.sleep(1.3)
            batch = [first]
            while len(batch) < 100:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            with self.io_lock:
                try:
                    if self.blocked:
                        raise RuntimeError('Нужно восстановить журнал')
                    try:
                        self.sheet.append_rows([r['row'] for r in batch],value_input_option='RAW')
                        committed = {r['row'][11] for r in batch}
                    except Exception:
                        try:
                            rows = self.sheet.get_all_values()[1:]
                            committed = {row[11] for row in rows if len(row)>=12}
                        except Exception:
                            self.blocked = True
                            raise
                    for r in batch:
                        if r['row'][11] in committed:
                            self.states[r['uid']] = r['state']
                        else:
                            r['error'] = RuntimeError('Запись не подтверждена')
                except Exception as exc:
                    for r in batch:
                        r['error'] = exc
                finally:
                    for r in batch:
                        r['done'].set()
                        self.queue.task_done()


class MessageHandler:
    def __init__(self, token, config=None):
        self.logger = logging.getLogger('telegram_bot')
        self.bot = telebot.TeleBot(token, threaded=True, num_threads=16)
        self.user_locks = {}
        self.locks_guard = threading.Lock()
        raw = os.environ['GOOGLE_CREDENTIALS']
        credentials = Credentials.from_service_account_info(json.loads(raw), scopes=['https://www.googleapis.com/auth/spreadsheets'])
        client = gspread.authorize(credentials)
        client.set_timeout(25)
        self.journal = Journal(client.open_by_key(os.environ['SPREADSHEET_ID'].strip()))
        self.admins = {int(x.strip()) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip()}
        self.cards = {}
        self.bot.message_handler(commands=['start','train','continue','menu','restart'])(self.command)
        self.bot.message_handler(commands=['admin','myid','links'])(self.admin)
        self.bot.callback_query_handler(func=lambda c: bool(c.data and c.data.startswith('v2:')))(self.callback)
        self.bot.callback_query_handler(func=lambda c: True)(self.old_button)

    def user_lock(self, uid):
        with self.locks_guard:
            return self.user_locks.setdefault(uid, threading.RLock())

    def ready(self):
        with self.journal.io_lock:
            if self.journal.blocked:
                self.journal.restore()

    def show(self, uid, s):
        text, actions = render(s)
        kb = types.InlineKeyboardMarkup(row_width=1)
        for label, action, value in actions:
            data = f"v2:{s['id']}:{s['rev']}:{action}:{value}"
            if len(data.encode()) > 64:
                raise ValueError('Слишком длинный callback')
            kb.add(types.InlineKeyboardButton(label,callback_data=data))
        # Новый экран появляется внизу чата: обратная связь не теряется выше.
        mid = self.cards.get(uid)
        sent = self.bot.send_message(uid,text,reply_markup=kb)
        self.cards[uid] = sent.message_id
        if mid:
            try:
                self.bot.edit_message_reply_markup(chat_id=uid,message_id=mid,reply_markup=None)
            except Exception:
                self.logger.info('Не удалось убрать прежние кнопки.')

    def failure(self, uid, exc):
        self.logger.warning('Операция не завершена: %s',type(exc).__name__)
        self.bot.send_message(uid,'Не удалось завершить действие. Твой последний подтверждённый шаг сохранён. Отправь /continue немного позже.')

    def command(self, message):
        if message.chat.type != 'private':
            self.bot.reply_to(message,'Тренировки проходят в личном чате с ботом. Открой его и отправь /start.')
            return
        uid = message.from_user.id
        with self.user_lock(uid):
            try:
                self.ready()
                parts = (message.text or '').split(maxsplit=1)
                command = parts[0].split('@')[0]
                previous = self.journal.states.get(uid)
                s = copy.deepcopy(previous) if previous else new_state(parts[1] if len(parts)>1 and parts[1] in CLASSES else None)
                if command == '/restart':
                    # Участники не могут перепроходить диагностику с заменой результата.
                    if s['enrolled_at']:
                        s['screen'] = 'settings'
                elif (command == '/menu' or (command == '/start' and len(parts) > 1 and parts[1] == 'resume')) and s['enrolled_at']:
                    s['screen'] = 'menu'
                s['last_interaction'] = now()
                s['rev'] += 1
                self.journal.commit(uid,s,{'event':'resume' if previous else 'registration_started'})
                self.show(uid,s)
            except Exception as exc:
                self.failure(uid,exc)

    def callback(self, call):
        if not call.message or call.message.chat.type != 'private':
            self.bot.answer_callback_query(call.id,'Открой личный чат с ботом.')
            return
        uid = call.from_user.id
        # Сразу закрываем индикатор, не подтверждая сохранение.
        try:
            self.bot.answer_callback_query(call.id)
        except Exception:
            pass
        with self.user_lock(uid):
            try:
                self.ready()
                parts = call.data.split(':')
                current = self.journal.states.get(uid)
                if len(parts) != 5 or not current or parts[1] != current['id'] or parts[2] != str(current['rev']):
                    if current:
                        self.cards[uid] = call.message.message_id
                        self.show(uid, current)
                    else:
                        self.bot.send_message(uid,'Открой тренировку командой /start.')
                    return
                s = copy.deepcopy(current)
                event = advance(s,parts[3],parts[4])
                self.journal.commit(uid,s,event)
                self.cards[uid] = call.message.message_id
                self.show(uid,s)
            except ValueError:
                self.bot.send_message(uid,'Действие пока недоступно. Открой /menu или /continue.')
            except Exception as exc:
                self.failure(uid,exc)

    def old_button(self, call):
        self.bot.answer_callback_query(call.id,'Это кнопка прежней версии. Открой /start в личном чате.')

    def admin(self, message):
        if message.chat.type != 'private':
            return
        uid = message.from_user.id
        if message.text.split('@')[0] == '/myid':
            self.bot.send_message(uid,f'Твой Telegram ID: {uid}')
            return
        if uid not in self.admins:
            self.bot.send_message(uid,'Сводка доступна организатору проекта.')
            return
        if message.text.split('@')[0] == '/links':
            name = self.bot.get_me().username
            links = [f'{label}: https://t.me/{name}?start={code}' for code,label in CLASSES.items()]
            self.bot.send_message(uid,'Ссылки на этот бот:\n\n'+'\n\n'.join(links))
            return
        with self.user_lock(uid):
            try:
                self.ready()
                states = list(self.journal.states.values())
                lines = ['Сводка по классам. Уникальные Telegram-аккаунты; тестирование исключено.','Класс: зарегистрированы | начали | входная | модули 1/2/3/4 | итоговая']
                for code,label in CLASSES.items():
                    if code == 'test': continue
                    group = [s for s in states if s['class_code']==code and s['enrolled_at']]
                    count = lambda a: sum(a in s['results'] for s in group)
                    lines.append(f"{label}: {len(group)} | {sum(bool(s['active']) for s in group)} | {count('pre')} | " + '/'.join(str(count('m'+str(i))) for i in range(4)) + f" | {count('post')}")
                lines.append('Начали, но ещё не закончили текущий этап: ' + str(sum(bool(s['active'] and s['active']['id'] not in s['results']) for s in states if s['class_code'] != 'test')))
                self.bot.send_message(uid,'\n'.join(lines))
            except Exception as exc:
                self.failure(uid,exc)

    def invitations(self):
        # Не более двух приглашений за календарную неделю, без ночных отправок.
        while True:
            time.sleep(30)
            local = datetime.now(ZoneInfo('Europe/Minsk'))
            if not 16 <= local.hour < 19:
                continue
            week = local.strftime('%G-W%V')
            for uid in list(self.journal.states):
                with self.user_lock(uid):
                    try:
                        self.ready()
                        current = self.journal.states.get(uid)
                        a = invitation_due(current, local) if current else None
                        if a is None: continue
                        s = copy.deepcopy(current)
                        s['notices'][a] = week
                        s['rev'] += 1
                        # Сначала резервируем отправку: при сбое не дублируем её.
                        self.journal.commit(uid,s,{'event':'invitation_reserved','activity':a})
                        name = self.bot.get_me().username
                        kb = types.InlineKeyboardMarkup()
                        kb.add(types.InlineKeyboardButton('Открыть тренировку',url=f'https://t.me/{name}?start=resume'))
                        self.bot.send_message(uid,'Доступно задание: '+title(a)+'. Можно пройти в удобное время. Приглашения отключаются в /menu → Настройки.',reply_markup=kb)
                    except Exception as exc:
                        self.logger.warning('Приглашение не подтверждено: %s',type(exc).__name__)

    def send_daily_message(self):
        pass  # Совместимость: прежние групповые рассылки выключены.

    def send_watch_message(self):
        pass

    def start(self):
        self.bot.remove_webhook()
        threading.Thread(target=self.invitations,daemon=True).start()
        self.logger.info('Курс v2 запущен. /start /continue /menu /admin')
        self.bot.infinity_polling(timeout=30,long_polling_timeout=25)
