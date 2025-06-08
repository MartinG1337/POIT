# Importovanie potrebných knižníc
from flask import Flask, render_template, request, redirect, url_for, jsonify  # Flask framework a jeho komponenty
import serial             # Komunikácia cez sériový port (napr. s Arduinom)
import threading          # Práca s vláknami (paralelné čítanie zo senzora)
import time               # Časové funkcie (napr. pauzy)
import sqlite3            # Práca s SQLite databázou
from datetime import datetime  # Práca s dátumom a časom
import csv                # Práca s CSV súbormi
import os                 # Práca so súborovým systémom

# Inicializácia Flask aplikácie
app = Flask(__name__)

# Globálne premenné
ser = None                        # Objekt sériového portu
reading_thread = None            # Vlákno pre čítanie dát zo senzora
reading_active = False           # Označuje, či sa má čítať zo senzora
monitoring_params = {}           # Nastavené parametre (napr. prah, interval)
monitoring_started = False       # Označuje, či beží monitorovanie
monitored_data = []              # Pole s monitorovanými dátami

# Konštanty pre veľkosť a názvy logovacích súborov
MAX_DATA_LENGTH = 100
MEASUREMENT_FILE = "measurements.csv"
PARAMETER_FILE = "parameters.csv"

# Inicializácia SQLite databázy (vytvorenie tabuliek ak neexistujú)
def init_db():
    conn = sqlite3.connect('monitoring.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            temperature REAL,
            humidity REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parameters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            threshold REAL,
            interval REAL
        )
    ''')
    conn.commit()
    conn.close()

# Vytvorenie CSV súborov s hlavičkami, ak ešte neexistujú
def init_log_files():
    if not os.path.exists(MEASUREMENT_FILE):
        with open(MEASUREMENT_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "temperature", "humidity"])
    if not os.path.exists(PARAMETER_FILE):
        with open(PARAMETER_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "threshold", "interval"])

# Zapísanie merania do CSV súboru
def log_measurement_to_file(temp, humidity):
    try:
        with open(MEASUREMENT_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now().isoformat(), temp, humidity])
            f.flush()  # Okamžité zapísanie
        print(f"✅ Written to CSV: Temp={temp}, Hum={humidity}")
    except Exception as e:
        print(f"CSV Write Error: {e}")

# Zapísanie parametrov do CSV súboru
def log_parameters_to_file(threshold, interval):
    with open(PARAMETER_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([datetime.now().isoformat(), threshold, interval])

# Zapísanie merania do databázy aj do CSV
def insert_measurement(temp, humidity):
    conn = sqlite3.connect('monitoring.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO measurements (timestamp, temperature, humidity) VALUES (?, ?, ?)",
                   (datetime.now().isoformat(), temp, humidity))
    conn.commit()
    conn.close()
    log_measurement_to_file(temp, humidity)
    print(f"✅ Inserted into DB and CSV: {temp}°C / {humidity}%")

# Zapísanie nastavených parametrov do databázy aj do CSV
def insert_parameters(threshold, interval):
    conn = sqlite3.connect('monitoring.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO parameters (timestamp, threshold, interval) VALUES (?, ?, ?)",
                   (datetime.now().isoformat(), threshold, interval))
    conn.commit()
    conn.close()
    log_parameters_to_file(threshold, interval)

# Vlákno, ktoré číta zo sériového portu dáta zo senzora
def read_serial():
    global ser, reading_active, monitoring_started, monitored_data
    last_temp = None
    last_hum = None

    while reading_active and ser:  # Pokiaľ čítanie prebieha
        try:
            raw_data = ser.readline().decode(errors='ignore').strip()
            print(f"📥 Received: {raw_data}")

            if monitoring_started:
                # Spracovanie teploty
                if "Temperature:" in raw_data:
                    try:
                        temp_str = raw_data.split(":")[1].strip().replace("°C", "")
                        last_temp = float(temp_str)
                        print(f"🌡️ Parsed temperature: {last_temp}")
                    except ValueError:
                        print("⚠️ Failed to parse temperature.")

                # Spracovanie vlhkosti
                elif "Humidity:" in raw_data:
                    try:
                        hum_str = raw_data.split(":")[1].strip().replace("%", "")
                        last_hum = float(hum_str)
                        print(f"💧 Parsed humidity: {last_hum}")
                    except ValueError:
                        print("⚠️ Failed to parse humidity.")

                # Ak sú obe hodnoty dostupné
                if last_temp is not None and last_hum is not None:
                    print("🟢 Both values received — writing to DB & CSV")
                    monitored_data.append((last_temp, last_hum))
                    insert_measurement(last_temp, last_hum)

                    # Udržiavame len posledných MAX_DATA_LENGTH záznamov
                    if len(monitored_data) > MAX_DATA_LENGTH:
                        monitored_data.pop(0)

                    # Kontrola prekročenia prahovej hodnoty
                    if monitoring_params.get("threshold") and last_temp > monitoring_params["threshold"]:
                        print(f"🚨 Threshold exceeded: {last_temp}°C > {monitoring_params['threshold']}°C")

                    last_temp = None
                    last_hum = None

        except Exception as e:
            print(f"Error reading serial: {e}")

        time.sleep(monitoring_params.get("interval", 1))  # Pauza medzi meraniami

# Flask routy pre web aplikáciu

@app.route('/')
def index():
    # Načítanie hlavnej stránky
    return render_template('index.html', params=monitoring_params, monitoring=monitoring_started, data=monitored_data)

@app.route('/data')
def get_data():
    # Vracia všetky dáta ako JSON
    return jsonify(monitored_data)

@app.route('/latest')
def get_latest_value():
    # Vracia poslednú nameranú hodnotu ako JSON
    if monitored_data:
        temp, hum = monitored_data[-1]
        return jsonify({"temperature": temp, "humidity": hum})
    else:
        return jsonify({"temperature": None, "humidity": None})

@app.route('/archive')
def archive():
    # Načíta posledných 100 záznamov z databázy
    conn = sqlite3.connect('monitoring.db')
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, temperature, humidity FROM measurements ORDER BY timestamp DESC LIMIT 100")
    rows = cursor.fetchall()
    conn.close()
    return jsonify(rows)

@app.route('/archive_file')
def archive_file():
    # Načíta posledných 100 záznamov z CSV súboru
    data = []
    try:
        with open(MEASUREMENT_FILE, 'r') as f:
            reader = csv.reader(f)
            next(reader)  # preskoč hlavičku
            data = list(reader)[-100:]
    except Exception as e:
        print(f"Error reading CSV file: {e}")
    return jsonify(data)

@app.route('/parameters')
def parameters():
    # Získaj posledných 50 záznamov parametrov z databázy
    conn = sqlite3.connect('monitoring.db')
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, threshold, interval FROM parameters ORDER BY timestamp DESC LIMIT 50")
    rows = cursor.fetchall()
    conn.close()
    return jsonify(rows)

@app.route('/parameters_file')
def parameters_file():
    # Získaj posledných 50 záznamov z CSV súboru
    data = []
    try:
        with open(PARAMETER_FILE, 'r') as f:
            reader = csv.reader(f)
            next(reader)
            data = list(reader)[-50:]
    except Exception as e:
        print(f"Error reading parameters file: {e}")
    return jsonify(data)

@app.route('/open', methods=['POST'])
def open_connection():
    # Otvorenie sériového portu a spustenie vlákna na čítanie dát
    global ser, reading_thread, reading_active
    try:
        ser = serial.Serial('/dev/ttyS0', 9600, timeout=1)
        reading_active = True
        reading_thread = threading.Thread(target=read_serial, daemon=True)
        reading_thread.start()
        print("🔌 Serial connection opened.")
    except Exception as e:
        print(f"Failed to open serial connection: {e}")
    return redirect(url_for('index'))

@app.route('/set_parameters', methods=['POST'])
def set_parameters():
    # Nastavenie nových parametrov (prahová teplota, interval)
    global monitoring_params
    threshold = request.form.get('threshold')
    interval = request.form.get('interval')
    monitoring_params['threshold'] = float(threshold) if threshold else None
    monitoring_params['interval'] = float(interval) if interval else 1.0
    insert_parameters(monitoring_params['threshold'], monitoring_params['interval'])
    return redirect(url_for('index'))

@app.route('/start', methods=['POST'])
def start_monitoring():
    # Spustenie monitorovania
    global monitoring_started
    monitoring_started = True
    print("▶️ Monitoring started.")
    return redirect(url_for('index'))

@app.route('/stop', methods=['POST'])
def stop_monitoring():
    # Zastavenie monitorovania
    global monitoring_started
    monitoring_started = False
    print("⏹️ Monitoring stopped.")
    return redirect(url_for('index'))

@app.route('/close', methods=['POST'])
def close_connection():
    # Zatvorenie sériového portu a resetovanie všetkých údajov
    global ser, reading_active, monitoring_started, monitored_data, monitoring_params
    monitoring_started = False
    reading_active = False
    monitored_data.clear()
    monitoring_params.clear()
    if ser and ser.is_open:
        ser.close()
        print("❌ Serial connection closed.")
    ser = None
    return redirect(url_for('index'))

# Spustenie aplikácie
if __name__ == '__main__':
    init_db()            # Inicializuj databázu
    init_log_files()     # Inicializuj CSV súbory
    app.run(host='0.0.0.0', port=5000)  # Spusti Flask server
