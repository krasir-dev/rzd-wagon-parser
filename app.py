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
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file, make_response
from dotenv import load_dotenv
import subprocess
import sys
import traceback

# ============================================
# ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ============================================
load_dotenv()

app = Flask(__name__)

# ============================================
# НАСТРОЙКИ CORS
# ============================================
@app.after_request
def add_cors_headers(response):
    """Добавляем CORS заголовки ко всем ответам."""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Accept')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    response.headers.add('Cache-Control', 'no-cache, no-store, must-revalidate')
    response.headers.add('Pragma', 'no-cache')
    response.headers.add('Expires', '0')
    return response

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
# НАСТРОЙКИ FLASK
# ============================================
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(hours=1)
app.config['JSON_AS_ASCII'] = False
app.config['JSON_SORT_KEYS'] = False

# ============================================
# ХРАНИЛИЩЕ ДЛЯ АСИНХРОННЫХ ЗАДАЧ
# ============================================
task_status = {}  # Единое хранилище для всех задач

# ============================================
# ЛОГИРОВАНИЕ
# ============================================
def log_message(msg, level="INFO"):
    """Функция для логирования с временной меткой."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level}: {msg}")
    sys.stdout.flush()

# ============================================
# ПРОВЕРКА И НАСТРОЙКА CHROMIUM
# ============================================
def setup_chromium():
    """Проверка наличия и настройка Chromium и chromedriver."""
    log_message("="*60, "DEBUG")
    log_message("🔧 ПРОВЕРКА CHROMIUM", "DEBUG")
    log_message("="*60, "DEBUG")

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
            os.chmod(chromedriver_path, 0o755)
            break

    if chromedriver_path:
        try:
            result = subprocess.run([chromedriver_path, "--version"],
                                  capture_output=True, text=True)
            log_message(f"  Версия: {result.stdout.strip()}", "DEBUG")
        except:
            pass

    log_message("="*60, "DEBUG")
    return chromium_binary, chromedriver_path

CHROMIUM_BINARY, CHROMEDRIVER_PATH = setup_chromium()

# ============================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ДРАЙВЕРОМ
# ============================================
def setup_driver(download_dir):
    """Настройка драйвера с использованием Chromium."""
    log_message("\n🚀 Запуск браузера...", "INFO")

    chrome_options = Options()
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

    if CHROMIUM_BINARY:
        chrome_options.binary_location = CHROMIUM_BINARY
        log_message(f"  Используется браузер: {CHROMIUM_BINARY}", "DEBUG")

    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled": True,
        "profile.default_content_setting_values.automatic_downloads": 1
    }
    chrome_options.add_experimental_option("prefs", prefs)

    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver_path = CHROMEDRIVER_PATH
    if not driver_path:
        possible_paths = ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver"]
        for path in possible_paths:
            if os.path.exists(path):
                driver_path = path
                break

    if not driver_path:
        raise Exception("ChromeDriver не найден!")

    log_message(f"  Используется ChromeDriver: {driver_path}", "INFO")

    service = Service(executable_path=driver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.maximize_window()
    log_message("✓ Браузер успешно запущен", "INFO")
    return driver

def close_driver(driver):
    """Безопасное закрытие драйвера."""
    if driver:
        try:
            driver.quit()
            log_message("✓ Браузер закрыт", "INFO")
        except:
            pass

# ============================================
# ФУНКЦИИ АВТОРИЗАЦИИ
# ============================================
def login(driver, username, password):
    """Авторизация на сайте."""
    log_message("\n🔑 Выполнение авторизации...", "INFO")

    try:
        driver.get(LOGIN_URL)
        time.sleep(3)

        login_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "query"))
        )
        login_field.clear()
        login_field.send_keys(username)

        password_field = driver.find_element(By.ID, "password")
        password_field.clear()
        password_field.send_keys(password)

        submit_button = driver.find_element(By.CSS_SELECTOR,
            "button.button.button_responsive.active[type='submit']")
        submit_button.click()

        time.sleep(5)

        if "sign_in" not in driver.current_url:
            log_message("✓ Авторизация успешна", "INFO")
            return True
        else:
            log_message("✗ Ошибка авторизации", "ERROR")
            return False

    except Exception as e:
        log_message(f"✗ Ошибка при авторизации: {e}", "ERROR")
        traceback.print_exc()
        return False

# ============================================
# ФУНКЦИИ ПАРСИНГА
# ============================================
def extract_document_number(text):
    """Извлекает только цифры из номера документа."""
    numbers = re.findall(r'\d+', text)
    return numbers[0] if numbers else None

def parse_wagon_dates(driver, wagon_data):
    """Парсинг дат для текущего вагона."""
    try:
        date_blocks = driver.find_elements(By.CSS_SELECTOR,
            "div.d-inline-block.mr-4_5.pb-3")

        for block in date_blocks:
            try:
                title = block.find_element(By.CSS_SELECTOR,
                    "span.font-weight-medium").text.strip()
                value = block.find_element(By.CSS_SELECTOR,
                    "div.font-weight-normal.mt-1.pt-1").text.strip().replace('\n', ' ')

                if title == "Подача":
                    wagon_data['Подача'] = value
                elif title == "Уборка":
                    wagon_data['Уборка'] = value
                elif "Возврат" in title:
                    wagon_data['Возврат на выставочный путь'] = value

            except Exception as e:
                log_message(f"  Ошибка парсинга блока: {e}", "DEBUG")

    except Exception as e:
        log_message(f"  Ошибка при поиске дат: {e}", "DEBUG")
        wagon_data['Подача'] = "Не найдено"
        wagon_data['Уборка'] = "Не найдено"
        wagon_data['Возврат на выставочный путь'] = "Не найдено"

def find_document_number(driver):
    """Поиск номера документа."""
    try:
        elements = driver.find_elements(By.XPATH, "//*[contains(text(), '№')]")
        for element in elements:
            text = element.text.strip()
            if '№' in text and any(char.isdigit() for char in text):
                extracted = extract_document_number(text)
                if extracted:
                    log_message(f"  ✓ Найден номер документа: {extracted}", "INFO")
                    return extracted
    except:
        pass
    return "Не найдено"

def parse_all_wagons(driver, document_number, task_id=None):
    """Парсинг данных по всем вагонам."""
    wagons_data = []

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "div.list-custom.list-custom_roster"))
        )
    except TimeoutException:
        log_message("✗ Список вагонов не найден", "ERROR")
        return wagons_data

    wagon_buttons = driver.find_elements(By.CSS_SELECTOR,
        "div.list-custom.list-custom_roster button.list-custom__item")
    doc_id = driver.current_url.rstrip('/').split('/')[-1]

    log_message(f"\n📋 Найдено вагонов: {len(wagon_buttons)}", "INFO")

    for i, button in enumerate(wagon_buttons, 1):
        if task_id and task_id in task_status:
            task_status[task_id]['message'] = f'Обработка вагона {i} из {len(wagon_buttons)}'

        try:
            wagon_number = button.find_element(By.CSS_SELECTOR,
                "span.list-custom__name").text.strip()

            driver.execute_script("arguments[0].scrollIntoView(true);", button)
            time.sleep(1)
            button.click()
            time.sleep(2)

            wagon_data = {}
            parse_wagon_dates(driver, wagon_data)

            wagon_data['Номер вагона'] = wagon_number
            wagon_data['ID документа'] = doc_id
            wagon_data['Номер документа'] = document_number

            wagons_data.append(wagon_data)
            log_message(f"  ✓ Вагон {i}: {wagon_number} обработан", "INFO")

        except Exception as e:
            log_message(f"  ✗ Ошибка при обработке вагона {i}: {e}", "ERROR")
            continue

    return wagons_data

def download_pdf(driver, download_dir, doc_id):
    """Скачивание печатной формы."""
    try:
        log_message("\n📥 Скачивание PDF...", "INFO")

        selectors = [
            "button.button_download",
            "//button[contains(text(), 'Печатная форма')]",
            "//button[contains(text(), 'печатная форма')]",
            "button[class*='download']"
        ]

        download_button = None
        for selector in selectors:
            try:
                if selector.startswith("//"):
                    download_button = driver.find_element(By.XPATH, selector)
                else:
                    download_button = driver.find_element(By.CSS_SELECTOR, selector)
                if download_button:
                    break
            except:
                continue

        if download_button:
            files_before = set(os.listdir(download_dir))

            driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
            time.sleep(1)
            download_button.click()

            timeout = 10
            for _ in range(timeout):
                time.sleep(1)
                files_after = set(os.listdir(download_dir))
                new_files = files_after - files_before
                if new_files:
                    pdf_files = [f for f in new_files if f.endswith('.pdf')]
                    if pdf_files:
                        old_path = os.path.join(download_dir, pdf_files[0])
                        new_filename = f"{doc_id}.pdf"
                        new_path = os.path.join(download_dir, new_filename)

                        if os.path.exists(new_path):
                            os.remove(new_path)
                        os.rename(old_path, new_path)
                        log_message(f"  ✓ PDF сохранен как: {new_filename}", "INFO")
                        return new_filename
            log_message("  ✗ PDF не скачался", "WARNING")
        else:
            log_message("  ✗ Кнопка 'Печатная форма' не найдена", "WARNING")

    except Exception as e:
        log_message(f"  ✗ Ошибка при скачивании PDF: {e}", "ERROR")
    return None

def process_document(driver, url, session_dir, task_id=None):
    """Обработка одного документа."""
    if '/' in url:
        doc_id = url.rstrip('/').split('/')[-1]
    else:
        doc_id = url
        url = f"https://cargolk.rzd.ru/documents/archive/memos/{doc_id}"

    log_message(f"\n📄 Обработка документа ID: {doc_id}", "INFO")

    driver.get(url)
    time.sleep(5)

    document_number = find_document_number(driver)
    wagons_data = parse_all_wagons(driver, document_number, task_id)
    pdf_filename = download_pdf(driver, session_dir, doc_id)

    return {
        'url': url,
        'doc_id': doc_id,
        'document_number': document_number,
        'wagons': wagons_data,
        'pdf': pdf_filename
    }

def create_zip_with_results(session_dir, all_results):
    """Создание ZIP архива с результатами."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"results_{timestamp}.zip"
    zip_path = os.path.join(session_dir, zip_filename)

    log_message(f"\n📦 Создание ZIP архива: {zip_filename}", "INFO")

    all_wagons = []
    for result in all_results:
        for wagon in result['wagons']:
            wagon['URL документа'] = result['url']
            wagon['PDF файл'] = result['pdf'] if result['pdf'] else 'Не скачан'
            all_wagons.append(wagon)

    if all_wagons:
        df = pd.DataFrame(all_wagons)

        excel_path = os.path.join(session_dir, "all_data.xlsx")
        df.to_excel(excel_path, index=False, engine='openpyxl')
        log_message(f"  ✓ Excel файл: {len(all_wagons)} записей", "INFO")

        csv_path = os.path.join(session_dir, "all_data.csv")
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        log_message(f"  ✓ CSV файл создан", "INFO")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        if os.path.exists(excel_path):
            zipf.write(excel_path, "all_data.xlsx")
        if os.path.exists(csv_path):
            zipf.write(csv_path, "all_data.csv")

        pdf_count = 0
        for result in all_results:
            if result['pdf']:
                pdf_path = os.path.join(session_dir, result['pdf'])
                if os.path.exists(pdf_path):
                    zipf.write(pdf_path, f"pdf/{result['pdf']}")
                    pdf_count += 1

        log_message(f"  ✓ Добавлено PDF: {pdf_count}", "INFO")

    zip_size = os.path.getsize(zip_path) / (1024 * 1024)
    log_message(f"  ✓ ZIP создан: {zip_filename} ({zip_size:.2f} MB)", "INFO")

    return zip_path

# ============================================
# АСИНХРОННАЯ ОБРАБОТКА ЗАДАЧ
# ============================================
def process_task(task_id, urls, session_dir):
    """Фоновая обработка задачи."""
    log_message(f"\n🚀 Запуск фоновой задачи {task_id}", "INFO")

    driver = None
    all_results = []

    try:
        # Статус уже создан в async_start_parsing, просто обновляем сообщение
        task_status[task_id]['message'] = 'Запуск браузера...'

        driver = setup_driver(session_dir)

        task_status[task_id]['message'] = 'Авторизация...'
        if not login(driver, USERNAME, PASSWORD):
            task_status[task_id] = {
                'status': 'error',
                'message': 'Ошибка авторизации',
                'created_at': task_status[task_id]['created_at']
            }
            return

        for i, url in enumerate(urls, 1):
            task_status[task_id]['message'] = f'Обработка документа {i} из {len(urls)}'
            try:
                result = process_document(driver, url, session_dir, task_id)
                all_results.append(result)
            except Exception as e:
                log_message(f"✗ Ошибка при обработке {url}: {e}", "ERROR")
                continue

        if not all_results:
            task_status[task_id] = {
                'status': 'error',
                'message': 'Не удалось обработать ни одного документа',
                'created_at': task_status[task_id]['created_at']
            }
            return

        task_status[task_id]['message'] = "Создание ZIP архива..."
        zip_path = create_zip_with_results(session_dir, all_results)

        task_status[task_id] = {
            'status': 'completed',
            'message': f'Готово! Обработано {len(all_results)} документов',
            'file': os.path.basename(zip_path),
            'created_at': task_status[task_id]['created_at']
        }

        log_message(f"\n✅ Задача {task_id} успешно завершена", "INFO")

    except Exception as e:
        log_message(f"\n❌ Ошибка в задаче {task_id}: {e}", "ERROR")
        traceback.print_exc()
        if task_id in task_status:
            task_status[task_id] = {
                'status': 'error',
                'message': str(e),
                'created_at': task_status[task_id]['created_at']
            }
    finally:
        close_driver(driver)

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
        'tasks': {
            'active': [k for k, v in task_status.items() if v.get('status') == 'processing'],
            'completed': [k for k, v in task_status.items() if v.get('status') == 'completed'],
            'total': len(task_status)
        }
    }
    return jsonify(info)

@app.route('/tasks')
def list_tasks():
    """Список всех активных задач."""
    return jsonify({
        'total': len(task_status),
        'tasks': {
            task_id: {
                'status': data.get('status'),
                'message': data.get('message', '')[:50] + '...' if len(data.get('message', '')) > 50 else data.get('message', ''),
                'created_at': data.get('created_at')
            }
            for task_id, data in task_status.items()
        }
    })

@app.route('/check_task/<task_id>')
def check_task(task_id):
    """Принудительная проверка задачи (для отладки)."""
    result = {
        'task_id': task_id,
        'exists': task_id in task_status,
        'task_data': task_status.get(task_id, None),
        'all_tasks': list(task_status.keys())
    }
    return jsonify(result)

# ============================================
# ОСНОВНЫЕ ЭНДПОИНТЫ
# ============================================
@app.route('/')
def index():
    """Главная страница."""
    return render_template('index.html')

@app.route('/async_start_parsing', methods=['POST'])
def async_start_parsing():
    """Асинхронный запуск парсинга."""
    try:
        urls_text = request.form.get('urls', '')
        log_message(f"📥 Получен запрос с urls: {urls_text[:100]}...", "INFO")

        urls = [url.strip() for url in urls_text.split('\n') if url.strip()]

        if not urls:
            return jsonify({'error': 'Введите хотя бы один URL'}), 400

        task_id = str(uuid.uuid4())
        session_dir = os.path.join(DOWNLOAD_DIR, f"task_{task_id}")
        os.makedirs(session_dir, exist_ok=True)

        log_message(f"✅ Создана задача {task_id} с {len(urls)} URL", "INFO")

        # Сразу добавляем задачу в статус
        task_status[task_id] = {
            'status': 'processing',
            'message': 'Запуск браузера...',
            'created_at': datetime.now().isoformat()
        }

        # Запускаем в отдельном потоке
        thread = threading.Thread(target=process_task, args=(task_id, urls, session_dir))
        thread.daemon = True
        thread.start()

        return jsonify({
            'task_id': task_id,
            'message': 'Задача запущена'
        })

    except Exception as e:
        log_message(f"❌ Ошибка при запуске задачи: {e}", "ERROR")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/task_status/<task_id>')
def task_status_endpoint(task_id):
    """Проверка статуса задачи."""
    log_message(f"🔍 Запрос статуса для task_id: {task_id}", "INFO")

    if task_id in task_status:
        return jsonify(task_status[task_id])
    else:
        log_message(f"❌ Задача {task_id} не найдена. Доступные задачи: {list(task_status.keys())}", "WARNING")
        return jsonify({
            'status': 'not_found',
            'message': 'Задача не найдена',
            'available_tasks': list(task_status.keys())[-5:]  # Показываем последние 5 задач
        })

@app.route('/download/<filename>')
def download_file(filename):
    """Скачивание файла."""
    for task_folder in os.listdir(DOWNLOAD_DIR):
        if task_folder.startswith('task_'):
            file_path = os.path.join(DOWNLOAD_DIR, task_folder, filename)
            if os.path.exists(file_path):
                response = make_response(send_file(file_path, as_attachment=True))
                response.headers.add('Cache-Control', 'no-cache, no-store, must-revalidate')
                return response

    return jsonify({'error': 'Файл не найден'}), 404

# ============================================
# ОБРАБОТЧИКИ ОШИБОК
# ============================================
@app.errorhandler(500)
def internal_error(error):
    log_message(f"500 error: {error}", "ERROR")
    return jsonify({'error': 'Внутренняя ошибка сервера', 'details': str(error)}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Ресурс не найден'}), 404

@app.errorhandler(Exception)
def handle_exception(e):
    log_message(f"❌ Необработанное исключение: {e}", "ERROR")
    traceback.print_exc()
    return jsonify({'error': 'Внутренняя ошибка сервера', 'details': str(e)}), 500

# ============================================
# ПЕРИОДИЧЕСКАЯ ОЧИСТКА СТАРЫХ ЗАДАЧ
# ============================================
def cleanup_old_tasks():
    """Очистка задач старше 24 часов."""
    while True:
        time.sleep(3600)  # Проверка каждый час
        current_time = datetime.now()
        to_delete = []

        for task_id, task_data in task_status.items():
            created_at = datetime.fromisoformat(task_data.get('created_at', '2000-01-01T00:00:00'))
            if (current_time - created_at) > timedelta(hours=24):
                to_delete.append(task_id)

        for task_id in to_delete:
            del task_status[task_id]
            log_message(f"🧹 Удалена старая задача: {task_id}", "INFO")

# Запуск очистки в фоне
cleanup_thread = threading.Thread(target=cleanup_old_tasks, daemon=True)
cleanup_thread.start()

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