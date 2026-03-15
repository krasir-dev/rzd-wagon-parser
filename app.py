import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
import os
import re
import zipfile
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
import subprocess
import shutil
import stat
import sys
import traceback

# ============================================
# ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ============================================
load_dotenv()

app = Flask(__name__)

# ============================================
# НАСТРОЙКИ
# ============================================
LOGIN_URL = "https://cargolk.rzd.ru/sign_in"
USERNAME = os.getenv('RZD_USERNAME')
PASSWORD = os.getenv('RZD_PASSWORD')

if not USERNAME or not PASSWORD:
    raise ValueError(
        "❌ Не заданы учетные данные!\n"
        "Создайте файл .env в папке проекта с содержимым:\n"
        "RZD_USERNAME=ваш_логин\n"
        "RZD_PASSWORD=ваш_пароль"
    )

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# ============================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ============================================
progress = {
    'total': 0,
    'current': 0,
    'status': 'idle',
    'message': ''
}

# ============================================
# ЛОГИРОВАНИЕ
# ============================================
def log_message(msg, level="INFO"):
    """Функция для логирования с временной меткой."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level}: {msg}")
    sys.stdout.flush()  # Принудительный сброс буфера

# ============================================
# ПРОВЕРКА И НАСТРОЙКА CHROMIUM
# ============================================
def setup_chromium():
    """Проверка наличия и настройка Chromium и chromedriver."""
    log_message("="*60, "DEBUG")
    log_message("🔧 ПРОВЕРКА CHROMIUM", "DEBUG")
    log_message("="*60, "DEBUG")

    # Поиск Chromium
    chromium_paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chrome"
    ]

    chromium_binary = None
    for path in chromium_paths:
        if os.path.exists(path):
            chromium_binary = path
            log_message(f"✓ Браузер найден: {chromium_binary}", "DEBUG")
            break

    if not chromium_binary:
        log_message("✗ Браузер не найден!", "WARNING")

    # Поиск ChromeDriver
    driver_paths = [
        "/usr/bin/chromedriver",
        "/usr/local/bin/chromedriver",
        "/usr/bin/chromium-driver",
        "/usr/lib/chromium/chromedriver",
        "/usr/lib/chromium-browser/chromedriver"
    ]

    chromedriver_path = None
    for path in driver_paths:
        if os.path.exists(path):
            chromedriver_path = path
            log_message(f"✓ ChromeDriver найден: {chromedriver_path}", "DEBUG")
            # Делаем исполняемым
            os.chmod(chromedriver_path, 0o755)
            break

    if not chromedriver_path:
        # Пробуем найти через which
        try:
            result = subprocess.run(["which", "chromedriver"], capture_output=True, text=True)
            if result.returncode == 0:
                chromedriver_path = result.stdout.strip()
                log_message(f"✓ ChromeDriver найден через which: {chromedriver_path}", "DEBUG")
                os.chmod(chromedriver_path, 0o755)
        except:
            pass

    if not chromedriver_path:
        log_message("✗ ChromeDriver не найден!", "ERROR")
    else:
        # Проверяем версию
        try:
            result = subprocess.run([chromedriver_path, "--version"],
                                  capture_output=True, text=True)
            log_message(f"  Версия: {result.stdout.strip()}", "DEBUG")
        except:
            pass

    log_message("="*60, "DEBUG")
    return chromium_binary, chromedriver_path

# Запускаем проверку при старте
log_message("🚀 ЗАПУСК ПРИЛОЖЕНИЯ", "INFO")
CHROMIUM_BINARY, CHROMEDRIVER_PATH = setup_chromium()

# ============================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ДРАЙВЕРОМ
# ============================================
def setup_driver(download_dir):
    """Настройка драйвера с использованием Chromium."""
    log_message("\n🚀 Запуск браузера...", "INFO")

    try:
        chrome_options = Options()

        # Обязательные аргументы для headless-режима в контейнере
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--remote-debugging-port=9222")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--disable-setuid-sandbox")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-logging")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("--silent")

        # Явное указание пути к Chromium, если найден
        if CHROMIUM_BINARY:
            chrome_options.binary_location = CHROMIUM_BINARY
            log_message(f"  Используется браузер: {CHROMIUM_BINARY}", "DEBUG")
        else:
            log_message("  Браузер не указан, используется стандартный", "WARNING")

        # Настройки для скачивания файлов
        prefs = {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "safebrowsing.enabled": True,
            "profile.default_content_setting_values.automatic_downloads": 1
        }
        chrome_options.add_experimental_option("prefs", prefs)

        # Скрываем автоматизацию
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        # User-Agent
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        # Определяем путь к ChromeDriver
        driver_path = CHROMEDRIVER_PATH

        if not driver_path:
            # Если не нашли при старте, ищем сейчас
            possible_paths = [
                "/usr/bin/chromedriver",
                "/usr/local/bin/chromedriver",
                "/usr/bin/chromium-driver"
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    driver_path = path
                    log_message(f"  Найден ChromeDriver: {driver_path}", "DEBUG")
                    break

        if not driver_path:
            error_msg = "❌ ChromeDriver не найден! Установите chromium-driver"
            log_message(error_msg, "ERROR")
            raise Exception(error_msg)

        log_message(f"  Используется ChromeDriver: {driver_path}", "INFO")

        # Создаем сервис и драйвер
        service = Service(executable_path=driver_path)

        try:
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.maximize_window()
            log_message("✓ Браузер успешно запущен", "INFO")
            return driver
        except Exception as e:
            log_message(f"✗ Ошибка запуска браузера: {e}", "ERROR")
            traceback.print_exc()

            # Пробуем с дополнительными аргументами
            log_message("  Пробуем с дополнительными аргументами...", "INFO")
            chrome_options.add_argument("--disable-web-security")
            chrome_options.add_argument("--allow-running-insecure-content")

            try:
                driver = webdriver.Chrome(service=service, options=chrome_options)
                driver.maximize_window()
                log_message("✓ Браузер успешно запущен (со второй попытки)", "INFO")
                return driver
            except Exception as e2:
                log_message(f"✗ Ошибка при второй попытке: {e2}", "ERROR")
                traceback.print_exc()
                raise e
    except Exception as e:
        log_message(f"❌ Критическая ошибка в setup_driver: {e}", "ERROR")
        traceback.print_exc()
        raise

def close_driver(driver):
    """Безопасное закрытие драйвера."""
    if driver:
        try:
            driver.quit()
            log_message("✓ Браузер закрыт", "INFO")
        except:
            pass

# ============================================
# ТЕСТОВЫЕ ЭНДПОИНТЫ
# ============================================
@app.route('/test')
def test():
    """Тестовый endpoint для проверки работы."""
    return jsonify({
        'status': 'ok',
        'message': 'Сервер работает',
        'chromium': CHROMIUM_BINARY,
        'chromedriver': CHROMEDRIVER_PATH,
        'python_version': sys.version,
        'time': datetime.now().isoformat()
    })

@app.route('/debug')
def debug():
    """Endpoint для отладки."""
    info = {
        'env': {
            'USERNAME': USERNAME,
            'DOWNLOAD_DIR': DOWNLOAD_DIR,
        },
        'paths': {
            'chromium': CHROMIUM_BINARY,
            'chromedriver': CHROMEDRIVER_PATH,
        },
        'progress': progress
    }
    return jsonify(info)

# ============================================
# ОБРАБОТЧИКИ ОШИБОК
# ============================================
@app.errorhandler(500)
def internal_error(error):
    """Возвращаем JSON при внутренней ошибке."""
    log_message(f"500 error: {error}", "ERROR")
    return jsonify({'error': 'Внутренняя ошибка сервера', 'details': str(error)}), 500

@app.errorhandler(404)
def not_found(error):
    """Возвращаем JSON при ошибке 404."""
    return jsonify({'error': 'Ресурс не найден'}), 404

@app.errorhandler(Exception)
def handle_exception(e):
    """Обработка всех исключений."""
    log_message(f"❌ Необработанное исключение: {e}", "ERROR")
    traceback.print_exc()
    return jsonify({'error': 'Внутренняя ошибка сервера', 'details': str(e)}), 500

# ============================================
# ОСНОВНЫЕ ФУНКЦИИ ПАРСИНГА
# ============================================
# ... (все остальные функции из вашего app.py остаются без изменений) ...
# Вставьте сюда все остальные функции: login, extract_document_number,
# parse_wagon_dates, find_document_number, parse_all_wagons, download_pdf,
# process_document, create_zip_with_results

# ============================================
# ВЕБ-ИНТЕРФЕЙС
# ============================================
@app.route('/')
def index():
    """Главная страница."""
    return render_template('index.html')

@app.route('/start_parsing', methods=['POST'])
def start_parsing():
    """Запуск парсинга."""
    global progress

    log_message("\n" + "="*60, "INFO")
    log_message("📥 ПОЛУЧЕН ЗАПРОС НА ПАРСИНГ", "INFO")
    log_message("="*60, "INFO")

    try:
        urls_text = request.form.get('urls', '')
        log_message(f"Получен текст: {urls_text[:100]}...", "DEBUG")

        urls = [url.strip() for url in urls_text.split('\n') if url.strip()]

        if not urls:
            log_message("✗ Нет URL для обработки", "WARNING")
            return jsonify({'error': 'Введите хотя бы один URL'}), 400

        log_message(f"✓ Обрабатывается URL: {len(urls)}", "INFO")

        session_id = str(uuid.uuid4())[:8]
        session_dir = os.path.join(DOWNLOAD_DIR, f"session_{session_id}")
        os.makedirs(session_dir, exist_ok=True)
        log_message(f"✓ Создана сессия: {session_id}", "INFO")

        progress = {
            'total': len(urls),
            'current': 0,
            'status': 'running',
            'message': 'Запуск парсинга...'
        }

        driver = None
        all_results = []

        try:
            log_message("\n🚀 Запуск браузера...", "INFO")
            driver = setup_driver(session_dir)

            log_message("\n🔑 Выполнение авторизации...", "INFO")
            if not login(driver, USERNAME, PASSWORD):
                log_message("✗ Ошибка авторизации", "ERROR")
                return jsonify({'error': 'Ошибка авторизации'}), 500

            for i, url in enumerate(urls, 1):
                log_message(f"\n📄 Обработка документа {i}/{len(urls)}", "INFO")
                progress['current'] = i
                progress['message'] = f'Обработка документа {i} из {len(urls)}'

                try:
                    result = process_document(driver, url, session_dir)
                    all_results.append(result)
                    log_message(f"  ✓ Документ обработан, найдено вагонов: {len(result['wagons'])}", "INFO")
                except Exception as e:
                    log_message(f"  ✗ Ошибка при обработке документа {url}: {e}", "ERROR")
                    traceback.print_exc()
                    continue

            if not all_results:
                return jsonify({'error': 'Не удалось обработать ни одного документа'}), 500

            log_message("\n📦 Создание ZIP архива...", "INFO")
            zip_path = create_zip_with_results(session_dir, all_results)

            progress['status'] = 'completed'
            progress['message'] = f'Готово! Обработано {len(all_results)} документов'

            log_message(f"\n✅ Успешно! ZIP файл: {os.path.basename(zip_path)}", "INFO")

            return jsonify({
                'success': True,
                'message': f'Обработано {len(all_results)} документов',
                'file': os.path.basename(zip_path)
            })

        except Exception as e:
            log_message(f"\n❌ Ошибка в процессе парсинга: {e}", "ERROR")
            traceback.print_exc()
            progress['status'] = 'error'
            progress['message'] = f'Ошибка: {str(e)}'
            return jsonify({'error': str(e)}), 500
        finally:
            close_driver(driver)

    except Exception as e:
        log_message(f"\n❌ Критическая ошибка: {e}", "ERROR")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/progress')
def get_progress():
    """Получение прогресса."""
    return jsonify(progress)

@app.route('/download/<filename>')
def download_file(filename):
    """Скачивание файла."""
    for session_folder in os.listdir(DOWNLOAD_DIR):
        if session_folder.startswith('session_'):
            file_path = os.path.join(DOWNLOAD_DIR, session_folder, filename)
            if os.path.exists(file_path):
                return send_file(file_path, as_attachment=True)

    return jsonify({'error': 'Файл не найден'}), 404

# ============================================
# ЗАПУСК ПРИЛОЖЕНИЯ
# ============================================
if __name__ == '__main__':
    log_message("\n" + "="*60, "INFO")
    log_message("🚀 ЗАПУСК ПРИЛОЖЕНИЯ", "INFO")
    log_message("="*60, "INFO")
    log_message(f"📁 Папка загрузок: {DOWNLOAD_DIR}", "INFO")
    log_message(f"👤 Пользователь: {USERNAME}", "INFO")
    log_message("="*60 + "\n", "INFO")

    app.run(debug=True, host='0.0.0.0', port=5000)