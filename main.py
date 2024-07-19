import telebot
from telebot import types
import yt_dlp as youtube_dl
import os
from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip
from moviepy.editor import VideoFileClip
import logging
import re
from collections import deque
from openai import OpenAI
from config import KONSPIROLOG_API_OPENAI, KONSPIROLOG_BOT_API
import time
import threading
from requests.exceptions import ReadTimeout, Timeout, ConnectionError
from telebot.apihelper import ApiTelegramException

API_TOKEN = KONSPIROLOG_BOT_API

# Инициализация клиента OpenAI
client = OpenAI(api_key=KONSPIROLOG_API_OPENAI, base_url="https://api.proxyapi.ru/openai/v1")

bot = telebot.TeleBot(API_TOKEN)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_MESSAGE_LENGTH = 4096  # Максимальный размер сообщения в Telegram

# Храним последние 20 сообщений (включая вопросы и ответы) для ученого-конспиролога
conversation_history_scientist = deque(maxlen=20)
# Храним последние 20 сообщений (включая вопросы и ответы) для конспиролога-любителя
conversation_history_amateur = deque(maxlen=20)

# Настройка логирования
logging.basicConfig(filename='bot_errors.log', level=logging.ERROR,
                    format='%(asctime)s %(message)s', encoding='utf-8')

def error_handler(func):
    def wrapper(*args, **kwargs):
        max_retries = 5
        retry_delay = 5  # seconds
        attempt = 0

        while attempt < max_retries:
            try:
                return func(*args, **kwargs)
            except (ReadTimeout, Timeout, ConnectionError) as e:
                attempt += 1
                logging.error(
                    f"Network error in {func.__name__}, attempt {attempt}/{max_retries}: {e}",
                    exc_info=True
                )
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    logging.error(f"All attempts failed for {func.__name__}.")
                    if args and isinstance(args[0], types.Message):
                        send_error_message(args[0].chat.id, "Спасибо за тестинг! Произошла сетевая ошибка, и мы бежим ее исправлять. Пожалуйста, расскажите о ней @bukrisdy, чтобы мы смогли все ошибки найти и обработать. Сейчас Вы можете перейти в главное меню и попробовать снова.")
                    elif kwargs.get('call') and isinstance(kwargs['call'], types.CallbackQuery):
                        send_error_message(kwargs['call'].message.chat.id, "Спасибо за тестинг! Произошла сетевая ошибка, и мы бежим ее исправлять. Пожалуйста, расскажите о ней @bukrisdy, чтобы мы смогли все ошибки найти и обработать. Сейчас Вы можете перейти в главное меню и попробовать снова.")
                    break
            except ApiTelegramException as e:
                if e.error_code == 403 and 'bot was blocked by the user' in e.result_json['description']:
                    user_id = args[0].chat.id if args and isinstance(args[0], types.Message) else 'unknown'
                    logging.error(f"Bot was blocked by the user with chat_id {user_id}")
                    # Дополнительные действия, например, удаление пользователя из базы данных
                else:
                    logging.error(
                        f"ApiTelegramException in {func.__name__}: {e}",
                        exc_info=True
                    )
                break
            except Exception as e:
                user_id = args[0].chat.id if args and isinstance(args[0], types.Message) else 'unknown'
                logging.error(
                    f"Error in {func.__name__} (user_id: {user_id}): {e}",
                    exc_info=True
                )
                if args and isinstance(args[0], types.Message):
                    send_error_message(args[0].chat.id, "Спасибо за тестинг! Произошла ошибка, и мы бежим ее исправлять. Пожалуйста, расскажите о ней @bukrisdy, чтобы мы смогли все ошибки найти и обработать. Сейчас Вы можете перейти в главное меню и попробовать снова.")
                elif kwargs.get('call') and isinstance(kwargs['call'], types.CallbackQuery):
                    send_error_message(kwargs['call'].message.chat.id, "Спасибо за тестинг! Произошла ошибка, и мы бежим ее исправлять. Пожалуйста, расскажите о ней @bukrisdy, чтобы мы смогли все ошибки найти и обработать. Сейчас Вы можете перейти в главное меню и попробовать снова.")
                break
    return wrapper

def send_error_message(chat_id, message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_main_menu = types.InlineKeyboardButton(text="Главное меню", callback_data="main_menu")
    markup.add(btn_main_menu)
    bot.send_message(chat_id, message, reply_markup=markup)

# Приветственное сообщение
@bot.message_handler(commands=['start'])
@error_handler
def send_welcome(message):
    welcome_text = (
        "Добро пожаловать в Konspirolog_bot. С моей помощью ты можешь скачать видео с YouTube и Twitter ⬇️, "
        "но самое главное:...\n\n...мы обсудим с тобой то, о чем все молчат!🕵️‍♂️"
    )
    bot.send_message(message.chat.id, welcome_text)
    send_main_menu(message.chat.id)

def send_main_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)  # Установка row_width для одной кнопки в строке
    btn1 = types.InlineKeyboardButton(text="Скачивать видео ⬇️", callback_data="download_video")
    btn2 = types.InlineKeyboardButton(text="Говорить о заговорах 🕵️‍♂️", callback_data="talk")
    markup.add(btn1)
    markup.add(btn2)
    bot.send_message(chat_id, "Выбери, что мы будем делать:", reply_markup=markup)

# Обработка выбора кнопок
@bot.callback_query_handler(func=lambda call: True)
@error_handler
def handle_query(call):
    if call.data == "download_video":
        msg = bot.send_message(call.message.chat.id, "Введите ссылку на видео с YouTube или Twitter:")
        bot.register_next_step_handler(msg, download_video)
    elif call.data == "talk":
        send_talk_options(call.message.chat.id)
    elif call.data == "retry_link":
        msg = bot.send_message(call.message.chat.id, "Введите ссылку на видео с YouTube или Twitter:")
        bot.register_next_step_handler(msg, download_video)
    elif call.data == "main_menu":
        send_main_menu(call.message.chat.id)
    elif call.data == "scientist":
        bot.send_message(call.message.chat.id, "👋 Привет! Я профессор конспирологии. Здесь ты узнаешь, "
                                               "как и почему появляются теории заговора, а также разберешься, почему они "
                                               "не выдерживают научной критики. 🧐 Задавай свои вопросы, "
                                               "и будем вместе искать истину🔍!")
        msg = bot.send_message(call.message.chat.id, "Начинай!")
        bot.register_next_step_handler(msg, handle_scientist_question)
    elif call.data == "amateur":
        bot.send_message(call.message.chat.id, "👋Привет! Я помогу тебе раскрыть тайные заговоры и скрытые истины.🕵️‍♂️Давай вместе поговорим о том, что от нас скрывают!🌍")
        msg = bot.send_message(call.message.chat.id, "Говори, только тихо!")
        bot.register_next_step_handler(msg, handle_amateur_question)
    elif call.data == "continue_talk_scientist":
        msg = bot.send_message(call.message.chat.id, "Продолжай...")
        bot.register_next_step_handler(msg, handle_scientist_question)
    elif call.data == "continue_talk_amateur":
        msg = bot.send_message(call.message.chat.id, "Продолжай...")
        bot.register_next_step_handler(msg, handle_amateur_question)

def send_talk_options(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton(text="Профессор конспирологии👨‍🏫", callback_data="scientist")
    btn2 = types.InlineKeyboardButton(text="Конспиролог-любитель🕵️‍", callback_data="amateur")
    btn3 = types.InlineKeyboardButton(text="Главное меню", callback_data="main_menu")
    markup.add(btn1)
    markup.add(btn2)
    markup.add(btn3)
    bot.send_message(chat_id, "Выберите собеседника:", reply_markup=markup)

@error_handler
def handle_scientist_question(message):
    user_id = message.from_user.id
    question = message.text
    try:
        bot.send_message(message.chat.id, "Посмотрим, что говорит наука...📖")

        # Добавляем новое сообщение пользователя в историю
        conversation_history_scientist.append({"role": "user", "content": question})

        answer = question_answer_from_ChatGPT(question, "scientist")
        send_long_message(message.chat.id, answer, "scientist")
    except Exception as e:
        logging.error(f"Ошибка при обработке вопроса. User ID: {user_id}, Question: {question}, Error: {e}")
        bot.send_message(message.chat.id, "Произошла ошибка при обработке вашего вопроса. Попробуйте снова.")
        send_main_menu(message.chat.id)

@error_handler
def handle_amateur_question(message):
    user_id = message.from_user.id
    question = message.text
    try:
        bot.send_message(message.chat.id, "Нас подслушивают! Подожди, отойду от вентиляции...👂")

        # Добавляем новое сообщение пользователя в историю
        conversation_history_amateur.append({"role": "user", "content": question})

        answer = question_answer_from_ChatGPT(question, "amateur")
        send_long_message(message.chat.id, answer, "amateur")
    except Exception as e:
        logging.error(f"Ошибка при обработке вопроса. User ID: {user_id}, Question: {question}, Error: {e}")
        bot.send_message(message.chat.id, "Произошла ошибка при обработке вашего вопроса. Попробуйте снова.")
        send_main_menu(message.chat.id)

def question_answer_from_ChatGPT(question, role):
    if role == "scientist":
        prompt = f"""Ты опытный ученый, специализирующийся на конспирологии.
        Твой ответ должен быть информативным, с научной точки зрения, и включать объяснение причин появления теории заговора.
        Отвечай только на русском, используя доступный и понятный язык. Если вопрос касается конкретной теории, предоставь научные объяснения и контекст."""
        messages = [{"role": "system", "content": prompt}] + list(conversation_history_scientist)
    else:
        prompt = f"""Ты любитель-конспиролог, который видит теории заговора во всем на свете и поддерживает их.
        Твои ответы должны быть яркими, эмоциональными и подкрепленными популярными теориями заговора.
        Отвечай только на русском, используя доступный и понятный язык. Если вопрос касается конкретной теории, предоставь свое мнение и аргументы в поддержку этой теории.
        Ограничь ответ 1000 знаками."""
        messages = [{"role": "system", "content": prompt}] + list(conversation_history_amateur)

    response = client.chat.completions.create(
        model="gpt-4",
        messages=messages
    )

    answer = response.choices[0].message.content

    # Сохраняем ответ в историю
    if role == "scientist":
        conversation_history_scientist.append({"role": "assistant", "content": answer})
    else:
        conversation_history_amateur.append({"role": "assistant", "content": answer})

    return answer

def send_long_message(chat_id, message_text, role):
    # Разбиваем сообщение на части, если оно превышает максимальную длину
    parts = [message_text[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(message_text), MAX_MESSAGE_LENGTH)]
    for part in parts:
        bot.send_message(chat_id, part)
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_continue_talk = types.InlineKeyboardButton(text="Продолжить разговор➡️", callback_data=f"continue_talk_{role}")
    btn_main_menu = types.InlineKeyboardButton(text="Главное меню", callback_data="main_menu")
    markup.add(btn_continue_talk)
    markup.add(btn_main_menu)
    bot.send_message(chat_id, "Выберите опцию:", reply_markup=markup)

# Функция для проверки ссылки
def is_valid_url(url):
    regex = re.compile(
        r'^(?:http|ftp)s?://'  # http:// или https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # доменное имя
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|'  # IPv4 адрес
        r'\[?[A-F0-9]*:[A-F0-9:]+\]?)'  # IPv6 адрес
        r'(?::\d+)?'  # порт
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, url) is not None

@error_handler
def download_video(message):
    url = message.text
    if not is_valid_url(url):
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn_retry = types.InlineKeyboardButton(text="Ввести другую ссылку", callback_data="retry_link")
        btn_main_menu = types.InlineKeyboardButton(text="Главное меню", callback_data="main_menu")
        markup.add(btn_retry)
        markup.add(btn_main_menu)
        bot.send_message(message.chat.id, "Введите действительную ссылку или вернитесь в главное меню.", reply_markup=markup)
        return

    bot.send_message(message.chat.id, "Спасибо за ссылку, начинаем скачивать.⬇️ Это может занять некоторое время, будьте терпеливы.🧘\n\n**Если размер файла превышает 50 Мб, то он будет разбит на части.**")

    user_id = message.from_user.id
    video_path = f'video_{user_id}.mp4'
    try:
        video_path = download_video_from_url(url, video_path)
        send_video_in_parts(message.chat.id, video_path)
    except Exception as e:
        error_message = f"Ошибка при скачивании видео. User ID: {user_id}, URL: {url}, Error: {e}"
        logging.error(error_message)
        bot.send_message(message.chat.id, f"Ошибка при скачивании видео: {e}")
        send_main_menu(message.chat.id)
    finally:
        # Попытка удаления файла в блоке finally
        try:
            os.remove(video_path)
        except Exception as e:
            logging.error(f"Ошибка при удалении файла. User ID: {user_id}, URL: {url}, Error: {e}")
            bot.send_message(message.chat.id, f"Ошибка при удалении файла: {e}")

# Общая функция для скачивания видео с YouTube и Twitter
def download_video_from_url(url, output_filename):
    if os.path.exists(output_filename):
        os.remove(output_filename)  # Удалить старый файл перед скачиванием нового
    ydl_opts = {
        'format': 'best',
        'outtmpl': output_filename,
    }
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return output_filename

# Функция для отправки видео по частям
def send_video_in_parts(chat_id, video_path):
    file_size = os.path.getsize(video_path)
    success = True
    try:
        if file_size <= MAX_FILE_SIZE:
            with open(video_path, 'rb') as video:
                bot.send_video(chat_id, video, timeout=1000)
        else:
            video = VideoFileClip(video_path)
            duration = video.duration
            num_parts = int(file_size / MAX_FILE_SIZE) + 1
            part_duration = duration / num_parts
            start = 0
            part_num = 1
            while start < duration:
                end = start + part_duration
                if end > duration:
                    end = duration
                part_filename = f'{video_path}_part_{part_num}.mp4'
                ffmpeg_extract_subclip(video_path, start, end, targetname=part_filename)
                part_size = os.path.getsize(part_filename)
                if part_size <= MAX_FILE_SIZE:
                    with open(part_filename, 'rb') as part_video:
                        bot.send_video(chat_id, part_video, timeout=1000)
                else:
                    bot.send_message(chat_id, f"Часть {part_num} видео превышает 50 МБ и не может быть отправлена.")
                os.remove(part_filename)
                start = end
                part_num += 1
            video.close()  # Закрываем VideoFileClip после использования
    except Exception as e:
        success = False
        error_message = f"Ошибка при отправке видео. Chat ID: {chat_id}, Error: {e}"
        logging.error(error_message)
        bot.send_message(chat_id, "Произошла ошибка, попробуйте снова.")
    finally:
        if success:
            bot.send_message(chat_id, "Все видео успешно отправлены.")
        send_main_menu(chat_id)

if __name__ == '__main__':
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Ошибка при запуске бота: {e}")
            time.sleep(5)  # Ждем перед повторной попыткой
