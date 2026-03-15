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
# ПРОВЕРКА И НАСТРОЙКА CHROMIUM
# ============================================
def setup_chromium():
    """Проверка наличия и настройка Chromium и chromedriver."""
    print("\n" + "="*60)
    print("🔧 ПРОВЕРКА CHROMIUM")
    print("="*60)

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
            print(f"✓ Браузер найден: {chromium_binary}")
            break

    if not chromium_binary:
        print("✗ Браузер не найден! Будет использован стандартный путь.")

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
            print(f"✓ ChromeDriver найден: {chromedriver_path}")
            # Делаем исполняемым
            os.chmod(chromedriver_path, 0o755)
            break

    if not chromedriver_path:
        # Пробуем найти через which
        try:
            result = subprocess.run(["which", "chromedriver"], capture_output=True, text=True)
            if result.returncode == 0:
                chromedriver_path = result.stdout.strip()
                print(f"✓ ChromeDriver найден через which: {chromedriver_path}")
                os.chmod(chromedriver_path, 0o755)
        except:
            pass

    if not chromedriver_path:
        print("✗ ChromeDriver не найден! Будет использован поиск при запуске.")

    # Проверка версий
    try:
        if chromium_binary:
            version_result = subprocess.run([chromium_binary, "--version"],
                                          capture_output=True, text=True)
            print(f"  Версия браузера: {version_result.stdout.strip()}")

        if chromedriver_path:
            driver_version = subprocess.run([chromedriver_path, "--version"],
                                          capture_output=True, text=True)
            print(f"  Версия ChromeDriver: {driver_version.stdout.strip()}")
    except Exception as e:
        print(f"  Не удалось определить версии: {e}")

    print("="*60)
    return chromium_binary, chromedriver_path

# Запускаем проверку при старте
CHROMIUM_BINARY, CHROMEDRIVER_PATH = setup_chromium()

# ============================================
# ФУНКЦИИ ДЛЯ РАБОТЫ С ДРАЙВЕРОМ
# ============================================
def setup_driver(download_dir):
    """Настройка драйвера с использованием Chromium."""
    print("\n🚀 Запуск браузера...")

    chrome_options = Options()

    # Обязательные аргументы для headless-режима в контейнере
    chrome_options.add_argument("--headless=new")  # Новый headless-режим
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-logging")
    chrome_options.add_argument("--log-level=3")  # Только ошибки
    chrome_options.add_argument("--silent")

    # Явное указание пути к Chromium, если найден
    if CHROMIUM_BINARY:
        chrome_options.binary_location = CHROMIUM_BINARY
        print(f"  Используется браузер: {CHROMIUM_BINARY}")

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
                break

    if not driver_path:
        # Последняя надежда - ищем в системе
        try:
            result = subprocess.run(["which", "chromedriver"], capture_output=True, text=True)
            if result.returncode == 0:
                driver_path = result.stdout.strip()
        except:
            pass

    if not driver_path:
        raise Exception("❌ ChromeDriver не найден! Установите chromium-driver")

    # Делаем драйвер исполняемым
    if not os.access(driver_path, os.X_OK):
        print(f"  Делаем файл исполняемым: {driver_path}")
        os.chmod(driver_path, 0o755)

    print(f"  Используется ChromeDriver: {driver_path}")

    # Создаем сервис и драйвер
    service = Service(executable_path=driver_path)

    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.maximize_window()
        print("✓ Браузер успешно запущен")
        return driver
    except Exception as e:
        print(f"✗ Ошибка запуска браузера: {e}")

        # Пробуем с дополнительными аргументами
        print("  Пробуем с дополнительными аргументами...")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-running-insecure-content")

        try:
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.maximize_window()
            print("✓ Браузер успешно запущен (со второй попытки)")
            return driver
        except Exception as e2:
            print(f"✗ Ошибка при второй попытке: {e2}")
            raise e

def close_driver(driver):
    """Безопасное закрытие драйвера."""
    if driver:
        try:
            driver.quit()
            print("✓ Браузер закрыт")
        except:
            pass

# ============================================
# ФУНКЦИИ АВТОРИЗАЦИИ
# ============================================
def login(driver, username, password):
    """Авторизация на сайте."""
    print("\n🔑 Авторизация...")

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
            print("✓ Авторизация успешна")
            return True
        else:
            print("✗ Ошибка авторизации")
            return False

    except Exception as e:
        print(f"✗ Ошибка при авторизации: {e}")
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
                print(f"  Ошибка парсинга блока: {e}")

    except Exception as e:
        print(f"  Ошибка при поиске дат: {e}")
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
                    print(f"  ✓ Найден номер документа: {extracted}")
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
        print("✗ Список вагонов не найден")
        return wagons_data

    wagon_buttons = driver.find_elements(By.CSS_SELECTOR,
        "div.list-custom.list-custom_roster button.list-custom__item")
    doc_id = driver.current_url.rstrip('/').split('/')[-1]

    progress['total'] = len(wagon_buttons)
    print(f"\n📋 Найдено вагонов: {len(wagon_buttons)}")

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
            print(f"  ✓ Вагон {i}: {wagon_number} обработан")

        except Exception as e:
            print(f"  ✗ Ошибка при обработке вагона {i}: {e}")
            continue

    return wagons_data

def download_pdf(driver, download_dir, doc_id):
    """Скачивание печатной формы."""
    try:
        print("\n📥 Скачивание PDF...")

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

            # Ждем скачивания
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
                        print(f"  ✓ PDF сохранен как: {new_filename}")
                        return new_filename
            print("  ✗ PDF не скачался")
        else:
            print("  ✗ Кнопка 'Печатная форма' не найдена")

    except Exception as e:
        print(f"  ✗ Ошибка при скачивании PDF: {e}")
    return None

def process_document(driver, url, session_dir):
    """Обработка одного документа."""
    global progress
    progress['message'] = f'Переход к документу: {url}'

    # Извлекаем ID из URL
    if '/' in url:
        doc_id = url.rstrip('/').split('/')[-1]
    else:
        doc_id = url
        url = f"https://cargolk.rzd.ru/documents/archive/memos/{doc_id}"

    print(f"\n📄 Обработка документа ID: {doc_id}")

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

    print(f"\n📦 Создание ZIP архива: {zip_filename}")

    # Сбор всех данных
    all_wagons = []
    for result in all_results:
        for wagon in result['wagons']:
            wagon['URL документа'] = result['url']
            wagon['PDF файл'] = result['pdf'] if result['pdf'] else 'Не скачан'
            all_wagons.append(wagon)

    if all_wagons:
        df = pd.DataFrame(all_wagons)

        # Сохраняем Excel
        excel_path = os.path.join(session_dir, "all_data.xlsx")
        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  ✓ Excel файл: {len(all_wagons)} записей")

        # Сохраняем CSV
        csv_path = os.path.join(session_dir, "all_data.csv")
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"  ✓ CSV файл создан")

    # Создаем ZIP
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

        print(f"  ✓ Добавлено PDF: {pdf_count}")

    zip_size = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"  ✓ ZIP создан: {zip_filename} ({zip_size:.2f} MB)")

    return zip_path

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

    urls_text = request.form.get('urls', '')
    urls = [url.strip() for url in urls_text.split('\n') if url.strip()]

    if not urls:
        return jsonify({'error': 'Введите хотя бы один URL'}), 400

    session_id = str(uuid.uuid4())[:8]
    session_dir = os.path.join(DOWNLOAD_DIR, f"session_{session_id}")
    os.makedirs(session_dir, exist_ok=True)

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
            progress['current'] = i
            progress['message'] = f'Обработка документа {i} из {len(urls)}'

            result = process_document(driver, url, session_dir)
            all_results.append(result)

        zip_path = create_zip_with_results(session_dir, all_results)

        progress['status'] = 'completed'
        progress['message'] = f'Готово! Обработано {len(urls)} документов'

        return jsonify({
            'success': True,
            'message': f'Обработано {len(urls)} документов',
            'file': os.path.basename(zip_path)
        })

    except Exception as e:
        progress['status'] = 'error'
        progress['message'] = f'Ошибка: {str(e)}'
        return jsonify({'error': str(e)}), 500
    finally:
        close_driver(driver)

@app.route('/progress')
def get_progress():
    """Получение прогресса."""
    global progress
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
    print("\n" + "="*60)
    print("🚀 ЗАПУСК ПРИЛОЖЕНИЯ")
    print("="*60)
    print(f"📁 Папка загрузок: {DOWNLOAD_DIR}")
    print(f"👤 Пользователь: {USERNAME}")
    print("="*60 + "\n")

    app.run(debug=True, host='0.0.0.0', port=5000)