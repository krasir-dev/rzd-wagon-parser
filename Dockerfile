FROM python:3.10-slim

# Устанавливаем Chromium и chromedriver
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Проверяем установку
RUN chromium --version && chromedriver --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

RUN mkdir -p /app/downloads

# Создаем симлинк для удобства
RUN ln -sf /usr/bin/chromedriver /usr/local/bin/chromedriver

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "120", "--workers", "2", "app:app"]