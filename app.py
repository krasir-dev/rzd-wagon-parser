import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException
import os
import re
import zipfile
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv


# Загружаем переменные окружения из .env файла
load_dotenv()

app = Flask(__name__)


# --- НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ---
LOGIN_URL = "https://cargolk.rzd.ru/sign_in"
USERNAME = os.getenv('RZD_USERNAME')
PASSWORD = os.getenv('RZD_PASSWORD')


# Проверяем наличие учетных данных
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


# Создаем папки, если их нет
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)


# Глобальная переменная для хранения прогресса
progress = {
    'total': 0,
    'current': 0,
    'status': 'idle',
    'message': ''
}


# ----------------- ФУНКЦИИ ПАРСИНГА -----------------

def setup_driver(download_dir):
    """Настройка драйвера Chrome."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.maximize_window()
    return driver


def login(driver, username, password):
    """Авторизация на сайте."""
    driver.get(LOGIN_URL)
    time.sleep(3)

    try:
        login_field = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "query")))
        login_field.clear()
        login_field.send_keys(username)

        password_field = driver.find_element(By.ID, "password")
        password_field.clear()
        password_field.send_keys(password)

        submit_button = driver.find_element(By.CSS_SELECTOR, "button.button.button_responsive.active[type='submit']")
        submit_button.click()
        time.sleep(5)
        return True
    except Exception as e:
        print(f"Ошибка авторизации: {e}")
        return False


def extract_document_number(text):
    """Извлекает только цифры из номера документа."""
    numbers = re.findall(r'\d+', text)
    if numbers:
        return numbers[0]
    return None


def parse_all_wagons(driver, document_number):
    """Парсинг данных по всем вагонам с переключением между ними."""
    wagons_data = []
    global progress

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.list-custom.list-custom_roster"))
        )
    except TimeoutException:
        return wagons_data

    wagon_buttons = driver.find_elements(By.CSS_SELECTOR, "div.list-custom.list-custom_roster button.list-custom__item")
    doc_id = driver.current_url.rstrip('/').split('/')[-1]

    progress['total'] = len(wagon_buttons)

    for i, button in enumerate(wagon_buttons, 1):
        progress['current'] = i
        progress['message'] = f'Обработка вагона {i} из {len(wagon_buttons)}'

        try:
            wagon_number = button.find_element(By.CSS_SELECTOR, "span.list-custom__name").text.strip()

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

        except Exception as e:
            print(f"Ошибка при обработке вагона: {e}")
            continue

    return wagons_data


def parse_wagon_dates(driver, wagon_data):
    """Парсинг дат для текущего вагона."""
    try:
        date_blocks = driver.find_elements(By.CSS_SELECTOR, "div.d-inline-block.mr-4_5.pb-3")

        for block in date_blocks:
            try:
                title = block.find_element(By.CSS_SELECTOR, "span.font-weight-medium").text.strip()
                value = block.find_element(By.CSS_SELECTOR, "div.font-weight-normal.mt-1.pt-1").text.strip().replace('\n', ' ')

                if title == "Подача":
                    wagon_data['Подача'] = value
                elif title == "Уборка":
                    wagon_data['Уборка'] = value
                elif "Возврат" in title:
                    wagon_data['Возврат на выставочный путь'] = value

            except Exception as e:
                print(f"Ошибка парсинга блока: {e}")

    except Exception as e:
        print(f"Ошибка при поиске дат: {e}")
        wagon_data['Подача'] = "Не найдено"
        wagon_data['Уборка'] = "Не найдено"
        wagon_data['Возврат на выставочный путь'] = "Не найдено"


def find_document_number(driver):
    """Парсинг номера документа."""
    try:
        # Ищем любой элемент, содержащий текст с № и цифрами
        elements = driver.find_elements(By.XPATH, "//*[contains(text(), '№')]")
        for element in elements:
            text = element.text.strip()
            if '№' in text and any(char.isdigit() for char in text):
                extracted = extract_document_number(text)
                if extracted:
                    doc_number = extracted
                    print(f"✓ Найден номер документа: {doc_number} (из текста: '{text}')")
                    return doc_number
    except:
        pass
    return "Не найдено"


def download_pdf(driver, download_dir, doc_id):
    """Скачивание печатной формы и переименование в ID документа."""
    try:
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
            # Запоминаем файлы до клика
            files_before = set(os.listdir(download_dir))

            driver.execute_script("arguments[0].scrollIntoView(true);", download_button)
            time.sleep(1)
            download_button.click()
            print(f"  ✓ Клик по кнопке 'Печатная форма' выполнен")

            # Ждем скачивания (до 10 секунд)
            timeout = 10
            new_files = None
            for _ in range(timeout):
                time.sleep(1)
                files_after = set(os.listdir(download_dir))
                new_files = files_after - files_before
                if new_files:
                    break

            if new_files:
                # Находим скачанный PDF
                pdf_files = [f for f in new_files if f.endswith('.pdf')]
                if pdf_files:
                    old_path = os.path.join(download_dir, pdf_files[0])
                    # Переименовываем в ID документа
                    new_filename = f"{doc_id}.pdf"
                    new_path = os.path.join(download_dir, new_filename)

                    # Если файл с таким именем уже есть, удаляем
                    if os.path.exists(new_path):
                        os.remove(new_path)

                    os.rename(old_path, new_path)
                    print(f"  ✓ PDF переименован в: {new_filename}")
                    return new_filename
            else:
                print("  ✗ PDF не скачался за 10 секунд")
    except Exception as e:
        print(f"  ✗ Ошибка при скачивании PDF: {e}")
    return None


def process_document(driver, url, session_dir):
    """Обработка одного документа."""
    global progress
    progress['message'] = f'Переход к документу: {url}'

    # Извлекаем ID из URL, если передан полный URL
    if '/' in url:
        doc_id = url.rstrip('/').split('/')[-1]
    else:
        doc_id = url
        url = f"https://cargolk.rzd.ru/documents/archive/memos/{doc_id}"

    print(f"\n📄 Обработка документа ID: {doc_id}")

    driver.get(url)
    time.sleep(5)

    # Находим номер документа
    document_number = find_document_number(driver)
    print(f"  ✓ Номер документа: {document_number}")

    # Парсим вагоны
    wagons_data = parse_all_wagons(driver, document_number)
    print(f"  ✓ Найдено вагонов: {len(wagons_data)}")

    # Скачиваем PDF (переименуется в ID документа)
    pdf_filename = download_pdf(driver, session_dir, doc_id)

    return {
        'url': url,
        'doc_id': doc_id,
        'document_number': document_number,
        'wagons': wagons_data,
        'pdf': pdf_filename
    }


def create_zip_with_results(session_dir, all_results):
    """Создание ZIP архива со всеми результатами (Excel, CSV и все PDF)."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"results_{timestamp}.zip"
    zip_path = os.path.join(session_dir, zip_filename)

    print(f"\n📦 Создание ZIP архива: {zip_filename}")

    # Создаем общий Excel файл со всеми данными
    all_wagons = []
    for result in all_results:
        for wagon in result['wagons']:
            wagon['URL документа'] = result['url']
            wagon['PDF файл'] = result['pdf'] if result['pdf'] else 'Не скачан'
            all_wagons.append(wagon)

    if all_wagons:
        df = pd.DataFrame(all_wagons)

        # Переупорядочиваем колонки для удобства
        columns_order = ['Номер вагона', 'Подача', 'Уборка', 'Возврат на выставочный путь',
                        'Номер документа', 'ID документа', 'URL документа', 'PDF файл']
        existing_columns = [col for col in columns_order if col in df.columns]
        df = df[existing_columns]

        # Сохраняем Excel
        excel_path = os.path.join(session_dir, "all_data.xlsx")
        df.to_excel(excel_path, index=False, engine='openpyxl')
        print(f"  ✓ Excel файл создан: all_data.xlsx ({len(all_wagons)} записей)")

        # Сохраняем CSV (как дополнительный формат)
        csv_path = os.path.join(session_dir, "all_data.csv")
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"  ✓ CSV файл создан: all_data.csv")

    # Создаем ZIP архив
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Добавляем Excel файл
        if os.path.exists(excel_path):
            zipf.write(excel_path, "all_data.xlsx")
            print(f"  ✓ Добавлен в ZIP: all_data.xlsx")

        # Добавляем CSV файл
        if os.path.exists(csv_path):
            zipf.write(csv_path, "all_data.csv")
            print(f"  ✓ Добавлен в ZIP: all_data.csv")

        # Добавляем все PDF файлы (уже переименованные в ID документа)
        pdf_count = 0
        for result in all_results:
            if result['pdf']:
                pdf_path = os.path.join(session_dir, result['pdf'])
                if os.path.exists(pdf_path):
                    # Сохраняем PDF в папку pdf внутри архива
                    arcname = f"pdf/{result['pdf']}"
                    zipf.write(pdf_path, arcname)
                    pdf_count += 1
                    print(f"  ✓ Добавлен в ZIP: pdf/{result['pdf']}")

        print(f"  ✓ Всего добавлено PDF: {pdf_count}")

    # Проверяем размер архива
    zip_size = os.path.getsize(zip_path) / (1024 * 1024)  # в MB
    print(f"  ✓ ZIP архив создан: {zip_filename} ({zip_size:.2f} MB)")

    return zip_path


def cleanup_old_files(session_dir, keep_hours=24):
    """Очистка старых файлов (оставляем только за последние 24 часа)."""
    try:
        now = time.time()
        for filename in os.listdir(session_dir):
            filepath = os.path.join(session_dir, filename)
            if os.path.isfile(filepath):
                file_age = now - os.path.getmtime(filepath)
                if file_age > keep_hours * 3600:  # Преобразуем часы в секунды
                    os.remove(filepath)
                    print(f"  🧹 Удален старый файл: {filename}")
    except Exception as e:
        print(f"Ошибка при очистке: {e}")


# ----------------- ВЕБ-ИНТЕРФЕЙС -----------------

@app.route('/')
def index():
    """Главная страница."""
    return render_template('index.html')


@app.route('/start_parsing', methods=['POST'])
def start_parsing():
    """Запуск парсинга."""
    global progress

    # Получаем список URL из формы
    urls_text = request.form.get('urls', '')
    urls = [url.strip() for url in urls_text.split('\n') if url.strip()]

    if not urls:
        return jsonify({'error': 'Введите хотя бы один URL'}), 400

    # Создаем уникальную папку для сессии
    session_id = str(uuid.uuid4())[:8]
    session_dir = os.path.join(DOWNLOAD_DIR, f"session_{session_id}")
    os.makedirs(session_dir, exist_ok=True)

    # Сбрасываем прогресс
    progress = {
        'total': len(urls),
        'current': 0,
        'status': 'running',
        'message': 'Запуск парсинга...'
    }

    # Запускаем обработку
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

        # Создаем ZIP архив
        zip_path = create_zip_with_results(session_dir, all_results)

        # Очищаем старые файлы
        cleanup_old_files(DOWNLOAD_DIR)

        progress['status'] = 'completed'
        progress['message'] = f'Готово! Обработано {len(urls)} документов'
        progress['result_file'] = os.path.basename(zip_path)
        progress['session_dir'] = os.path.basename(session_dir)

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
        if driver:
            driver.quit()


@app.route('/progress')
def get_progress():
    """Получение текущего прогресса."""
    global progress
    return jsonify(progress)


@app.route('/download/<filename>')
def download_file(filename):
    """Скачивание файла."""
    # Ищем файл в папках сессий
    for session_folder in os.listdir(DOWNLOAD_DIR):
        if session_folder.startswith('session_'):
            file_path = os.path.join(DOWNLOAD_DIR, session_folder, filename)
            if os.path.exists(file_path):
                return send_file(file_path, as_attachment=True)

    return jsonify({'error': 'Файл не найден'}), 404


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5028)