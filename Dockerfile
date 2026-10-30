FROM python:3.11-slim

# Çalışma dizinini belirle
WORKDIR /app

# Önce gereksinim dosyasını kopyala (Docker önbelleklemesi için en iyisi budur)
COPY requirements.txt .

# Bağımlılıkları yükle
RUN pip install --no-cache-dir -r requirements.txt

# Projedeki tüm dosyaları konteynere kopyala
COPY . .

# Render'ın dış dünyadan istek alabilmesi için portu aç
EXPOSE 5000

# Flask uygulamasını başlat (Render dinamik port atayabileceği için $PORT değişkenini de destekleyecek şekilde güncelledik)
CMD python app.py
