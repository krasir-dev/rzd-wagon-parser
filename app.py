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

def parse_all_wagons(driver, document_number):
    """Парсинг данных по всем вагонам."""
    wagons_data = []
    global progress

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

    progress['total'] = len(wagon_buttons)
    log_message(f"\n📋 Найдено вагонов: {len(wagon_buttons)}", "INFO")

    for i, button in enumerate(wagon_buttons, 1):
        progress['current'] = i
        progress['message'] = f'Обработка вагона {i} из {len(wagon_buttons)}'

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

def process_document(driver, url, session_dir):
    """Обработка одного документа."""
    global progress
    progress['message'] = f'Переход к документу: {url}'

    if '/' in url:
        doc_id = url.rstrip('/').split('/')[-1]
    else:
        doc_id = url
        url = f"https://cargolk.rzd.ru/documents/archive/memos/{doc_id}"

    log_message(f"\n📄 Обработка документа ID: {doc_id}", "INFO")

    driver.get(url)
    time.sleep(5)

    document_number = find_document_number(driver)
    wagons_data = parse_all_wagons(driver, document_number)
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
            driver = setup_driver(session_dir)

            if not login(driver, USERNAME, PASSWORD):
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
                    continue

            if not all_results:
                return jsonify({'error': 'Не удалось обработать ни одного документа'}), 500

            zip_path = create_zip_with_results(session_dir, all_results)

            progress['status'] = 'completed'
            progress['message'] = f'Готово! Обработано {len(all_results)} документов'
            progress['result_file'] = os.path.basename(zip_path)

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