#changeee
import network
import socket
import machine
import utime
from time import sleep, localtime
from machine import Pin, ADC
import gc
import urequests
import uos
import json

# Wi-Fi credentials
SSID = 'VM5792329'
PASSWORD = 'rk2dqJpcGyjd'

# Constants
WATCHDOG_TIMEOUT = 180
MAX_PUMP_TIME = 60
COOLDOWN_TIME = 30
DRY_VALUE = 43000
WET_VALUE = 50000
MOISTURE_THRESHOLD = 30.0

# Set up hardware components
moisture_pin = ADC(26)
pump_pin = Pin(16, Pin.OUT)
led = machine.Pin("LED", machine.Pin.OUT)
temp_sensor = ADC(4)
conversion_factor = 3.3 / 65535

# Global state
data_points = []
pump_log = []
last_check_time = utime.time()
last_pump_activation = 0
last_pump_deactivation = 0
cooldown_active = False
pump_state = False

# Custom logging function
def log(message):
    timestamp = "{:02}/{:02}/{} {:02}:{:02}:{:02}".format(*localtime()[:6])
    with open('watering_log.txt', 'a') as log_file:
        log_file.write(f"{timestamp} - {message}\n")
    print(message)

# Read functions
def read_moisture():
    try:
        moisture_value = moisture_pin.read_u16()
        inverted_moisture = 65535 - moisture_value
        moisture_percentage = ((inverted_moisture - DRY_VALUE) / (WET_VALUE - DRY_VALUE)) * 100
        return max(0, min(moisture_percentage, 100))
    except Exception as e:
        log(f"Error reading moisture: {e}")
        return 0

def read_temperature():
    try:
        reading = temp_sensor.read_u16() * conversion_factor
        return 27 - (reading - 0.706) / 0.001721
    except Exception as e:
        log(f"Error reading temperature: {e}")
        return 0

# Control functions
def control_led(state):
    try:
        led.value(state)
        log(f"LED {'on' if state else 'off'}")
    except Exception as e:
        log(f"Error controlling LED: {e}")

def activate_pump():
    global pump_state, last_pump_activation
    if not pump_state:
        pump_pin.on()
        pump_state = True
        last_pump_activation = utime.time()
        pump_log.append(f"Pump Activated ({localtime_to_string(localtime())} for 0 seconds)")
        if len(pump_log) > 10:
            pump_log.pop(0)
        log("Pump activated")

def deactivate_pump():
    global pump_state, cooldown_active, last_pump_activation, last_pump_deactivation
    if pump_state:
        pump_pin.off()
        pump_state = False
        duration = int(utime.time() - last_pump_activation)
        pump_log[-1] = pump_log[-1].replace("0 seconds", f"{duration} seconds")
        cooldown_active = True
        last_pump_deactivation = utime.time()
        log("Pump deactivated")

# Wi-Fi functions
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    
    max_wait = 20
    while max_wait > 0:
        if wlan.status() < 0 or wlan.status() >= 3:
            break
        max_wait -= 1
        print('waiting for connection...')
        sleep(3)

    if wlan.status() != 3:
        raise RuntimeError('network connection failed')
    else:
        print('connected')
        status = wlan.ifconfig()
        print('ip = ' + status[0])
        return status[0]

def check_wifi_connection():
    wlan = network.WLAN(network.STA_IF)
    if not wlan.isconnected():
        print("Lost Wi-Fi connection. Attempting to reconnect...")
        connect_wifi()

# Socket functions
def open_socket(ip):
    address = (ip, 80)
    connection = socket.socket()
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    connection.bind(address)
    connection.listen(1)
    return connection

# Request handling functions
def handle_request(request_path):
    global MOISTURE_THRESHOLD

    log(f"Handling request for {request_path}")

    try:
        if request_path == '/':
            temperature = read_temperature()
            moisture = read_moisture()
            webpage_content = generate_webpage(temperature, "ON" if pump_state else "OFF", moisture, False, data_points, MOISTURE_THRESHOLD)
            return '200 OK', webpage_content
        elif request_path.startswith('/lighton'):
            control_led(True)
            return '200 OK', None
        elif request_path.startswith('/lightoff'):
            control_led(False)
            return '200 OK', None
        elif request_path.startswith('/pump?action=on'):
            activate_pump()
            return '200 OK', None
        elif request_path.startswith('/pump?action=off'):
            deactivate_pump()
            return '200 OK', None
        elif request_path.startswith('/threshold'):
            threshold_value = request_path.split('=')[1]
            try:
                MOISTURE_THRESHOLD = float(threshold_value)
                return '200 OK', None
            except ValueError:
                return '400 Bad Request', 'Invalid threshold value'
        elif request_path.startswith('/data'):
            moisture = read_moisture()
            temperature = read_temperature()
            response = json.dumps({"temperature": temperature, "moisture": moisture})
            return '200 OK', response
        elif request_path.startswith('/pumplog'):
            pump_log_html = "".join([f"<p>{entry}</p>" for entry in pump_log])
            return '200 OK', pump_log_html
        elif request_path.startswith('/update'):
            update_status = fetch_and_update()
            response = json.dumps({"message": update_status})
            return '200 OK', response
        else:
            return '404 Not Found', '<h1>404 Not Found</h1>'
    except Exception as e:
        log(f"Error handling request {request_path}: {e}")
        return '500 Internal Server Error', '<h1>500 Internal Server Error</h1>'

# Fetch and update function
def fetch_and_update():
    url = "https://raw.githubusercontent.com/nomad-49/auto-watering2/main/list_directory_file_size.py"
    local_file = "main.py"
    temp_file = "temp_main.py"
    
    try:
        log("Checking for updates.")
        gc.collect()
        response = urequests.get(url)
        if response.status_code == 200:
            with open(temp_file, 'wb') as f:
                f.write(response.content)
            response.close()
            
            with open(temp_file, 'rb') as f:
                remote_code = f.read()

            try:
                with open(local_file, 'rb') as f:
                    local_code = f.read()
            except OSError:
                local_code = b""

            if remote_code != local_code:
                log("Update found. Updating the local file.")
                with open(local_file, 'wb') as f:
                    f.write(remote_code)
                log("Restarting the device to run the updated code.")
                uos.remove(temp_file)
                machine.reset()
            else:
                log("No updates found.")
                return "No new software available"
        else:
            log(f"Failed to fetch the file. Status code: {response.status_code}")
            return "Failed to fetch the update"
    except Exception as e:
        log(f"Error fetching or updating the file: {str(e)}")
        return f"Error: {str(e)}"
    finally:
        try:
            uos.remove(temp_file)
        except OSError:
            pass
        gc.collect()

# Helper functions
def localtime_to_string(time_tuple):
    return "{:02}/{:02}/{} at {:02}:{:02}:{:02}".format(time_tuple[2], time_tuple[1], time_tuple[0], time_tuple[3], time_tuple[4], time_tuple[5])

# Webpage generation function
def generate_webpage(temperature, state, moisture, auto_water, data_points, threshold, update_message=""):
    auto_water_status = "ON" if not auto_water else "OFF"
    data_json = json.dumps(data_points[-60:])
    led_color = "lightgreen" if state == "ON" else "darkgrey"
    temperature_color = "black"
    if temperature > 30:
        temperature_color = "#ff6666"
    elif temperature < 5:
        temperature_color = "#6666ff"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Auto Watering System</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: 'Avenir Next LT Pro', sans-serif;
                background-color: #1affd1;
                color: #000;
                margin: 0;
                padding: 0;
                text-align: left;
                padding-left: 10px;
            }}
            h1, p {{
                color: #000;
            }}
            h1, #temperature {{
                margin-left: 10px;
            }}
            .control-box {{
                border: 3px solid #000;
                padding: 10px;
                margin: 10px 0;
                text-align: center;
                width: 90%;
                max-width: 400px;
                box-sizing: border-box;
            }}
            .control-title {{
                font-weight: bold;
                text-align: center;
                margin-bottom: 10px;
            }}
            .inline-buttons {{
                display: flex;
                justify-content: center;
                align-items: center;
            }}
            .chart-container {{
                width: 90%;
                max-width: 600px;
                margin: 0;
            }}
            .led-status {{
                display: inline-block;
                width: 20px;
                height: 20px;
                background-color: {led_color};
                border-radius: 50%;
                margin-right: 10px;
            }}
            #temperature {{
                color: {temperature_color};
                font-size: 1.5em;
                margin-bottom: 10px;
            }}
            .pump-log {{
                margin-top: 20px;
            }}
            .update-message {{
                color: red;
                font-weight: bold;
            }}
        </style>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script>
            var dataPoints = {data_json};
            var chart;
            var moistureThreshold = {threshold};

            function refreshData() {{
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/data', true);
                xhr.onload = function() {{
                    if (xhr.status == 200) {{
                        var data = JSON.parse(xhr.responseText);
                        var temperatureElement = document.getElementById('temperature');
                        temperatureElement.innerHTML = data.temperature + ' &deg;C';
                        if (data.temperature > 30) {{
                            temperatureElement.style.color = '#ff6666'; 
                        }} else if (data.temperature < 5) {{
                            temperatureElement.style.color = '#6666ff';
                        }} else {{
                            temperatureElement.style.color = 'black';
                        }}
                        document.getElementById('moisture').innerHTML = data.moisture + '%';
                        var currentTime = new Date().toLocaleTimeString('en-GB', {{ hour12: false }});
                        dataPoints.push({{time: currentTime, temperature: data.temperature, moisture: data.moisture}});
                        if (dataPoints.length > 60) dataPoints.shift();
                        updateChart();
                    }}
                }};
                xhr.send();
            }}

            function refreshPumpLog() {{
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/pumplog', true);
                xhr.onload = function() {{
                    if (xhr.status == 200) {{
                        var pumpLogElement = document.getElementById('pump-log-entries');
                        pumpLogElement.innerHTML = xhr.responseText;
                    }}
                }};
                xhr.send();
            }}

            function controlLED(action) {{
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/' + action, true);
                xhr.onload = function() {{
                    if (xhr.status == 200) {{
                        console.log(action + ' action completed');
                        var ledCircle = document.getElementById('led-status');
                        ledCircle.style.backgroundColor = (action === 'lighton') ? 'lightgreen' : 'darkgrey';
                    }}
                }};
                xhr.send();
            }}

            function controlPump(action) {{
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/pump?action=' + action, true);
                xhr.onload = function() {{
                    if (xhr.status == 200) {{
                        console.log('Pump ' + action + ' action completed');
                        document.getElementById('auto-water-status').innerHTML = 'OFF';
                        refreshPumpLog();
                    }}
                }};
                xhr.send();
            }}

            function autowaterControl() {{
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/autowater', true);
                xhr.onload = function() {{
                    if (xhr.status == 200) {{
                        console.log('Autowater action completed');
                        document.getElementById('auto-water-status').innerHTML = 'ON';
                    }}
                }};
                xhr.send();
            }}

            function updateThreshold() {{
                var thresholdInput = document.getElementById('threshold-input').value;
                if (thresholdInput && !isNaN(thresholdInput) && thresholdInput >= 0 && thresholdInput <= 100) {{
                    moistureThreshold = parseFloat(thresholdInput);
                    var xhr = new XMLHttpRequest();
                    xhr.open('GET', '/threshold?value=' + moistureThreshold, true);
                    xhr.onload = function() {{
                        if (xhr.status == 200) {{
                            console.log('Threshold updated');
                            updateChart();
                        }}
                    }};
                    xhr.send();
                }} else {{
                    alert('Please enter a valid number between 0 and 100.');
                }}
            }}

            function initializeChart() {{
                var ctx = document.getElementById('chart').getContext('2d');
                chart = new Chart(ctx, {{
                    type: 'line',
                    data: {{
                        labels: dataPoints.map(dp => dp.time),
                        datasets: [
                            {{
                                label: 'Temperature',
                                data: dataPoints.map(dp => dp.temperature),
                                borderColor: 'rgba(255, 99, 132, 0.6)', 
                                backgroundColor: 'rgba(255, 99, 132, 0.2)', 
                                borderWidth: 1,
                                fill: false,
                                pointStyle: 'circle'
                            }},
                            {{
                                label: 'Moisture (%)',
                                data: dataPoints.map(dp => dp.moisture),
                                borderColor: 'rgba(54, 162, 235, 0.6)', 
                                backgroundColor: 'rgba(54, 162, 235, 0.2)', 
                                borderWidth: 1,
                                fill: false,
                                pointStyle: 'circle'
                            }},
                            {{
                                label: 'Pump Threshold',
                                data: dataPoints.map(dp => moistureThreshold),
                                borderColor: 'rgba(75, 75, 75, 1)', 
                                borderWidth: 2,
                                borderDash: [5, 5],
                                fill: false,
                                pointRadius: 0,
                                pointStyle: 'line'
                            }}
                        ]
                    }},
                    options: {{
                        scales: {{
                            x: {{
                                type: 'category',
                                title: {{
                                    display: true,
                                    text: 'Time'
                                }}
                            }},
                            y: {{
                                beginAtZero: true,
                                min: 0,
                                max: 100,
                                title: {{
                                    display: true,
                                    text: 'Value'
                                }}
                            }}
                        }},
                        plugins: {{
                            legend: {{
                                labels: {{
                                    usePointStyle: true
                                }}
                            }}
                        }}
                    }}
                }});
            }}

            function updateChart() {{
                chart.data.labels = dataPoints.map(dp => dp.time);
                chart.data.datasets[0].data = dataPoints.map(dp => dp.temperature);
                chart.data.datasets[1].data = dataPoints.map(dp => dp.moisture);
                chart.data.datasets[2].data = dataPoints.map(dp => moistureThreshold);
                chart.update();
            }}

            function manualUpdate() {{
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/update', true);
                xhr.onload = function() {{
                    if (xhr.status == 200) {{
                        var response = JSON.parse(xhr.responseText);
                        document.getElementById('update-message').innerText = response.message;
                    }}
                }};
                xhr.send();
            }}

            window.onload = function() {{
                initializeChart(); 
                refreshData(); 
                refreshPumpLog(); 
                setInterval(refreshData, 5000); 
                setInterval(refreshPumpLog, 5000); 
                document.getElementById('threshold-input').addEventListener('change', updateThreshold);
            }};
        </script>
    </head>
    <body>
        <h1>Auto Watering System</h1>
        <p id="temperature">{temperature:.1f} &deg;C</p>
        <div class="control-box">
            <div class="control-title">LED</div>
            <div class="inline-buttons">
                <span id="led-status" class="led-status"></span>
                <button onclick="controlLED('lighton')">Light on</button>
                <button onclick="controlLED('lightoff')">Light off</button>
            </div>
        </div>
        <div class="control-box">
            <div class="control-title">Pump Control</div>
            <div class="inline-buttons">
                <button onclick="controlPump('on')">Pump On</button>
                <button onclick="controlPump('off')">Pump Off</button>
                <button onclick="autowaterControl()">Autowater</button>
            </div>
        </div>
        <p>Moisture Level: <span id="moisture">{moisture:.2f}%</span></p>
        <p>Moisture Threshold (for Pump): <input type="text" id="threshold-input" value="{threshold}" size="3" /> %</p>
        <p>Automatic Watering is <span id="auto-water-status">{auto_water_status}</span></p>
        <div class="chart-container">
            <canvas id="chart"></canvas>
        </div>
        <div class="pump-log" id="pump-log">
            <h2>Pump Activation Log</h2>
            <div id="pump-log-entries"></div>
        </div>
        <div class="control-box">
            <div class="control-title">Software Update</div>
            <div class="inline-buttons">
                <button onclick="manualUpdate()">Software Update</button>
            </div>
            <p id="update-message" class="update-message">{update_message}</p>
        </div>
    </body>
    </html>
    """
    return str(html)

# Initialization functions
def initial_check():
    moisture = read_moisture()
    if moisture < MOISTURE_THRESHOLD:
        activate_pump()

def initialize_system():
    global last_temperature_update
    last_temperature_update = utime.time()
    
    initial_check()

    ip = connect_wifi()
    connection = open_socket(ip)
    return connection

# Main loop
def main():
    global last_check_time
    connection = initialize_system()

    while True:
        try:
            current_time = utime.time()
            
            client, addr = connection.accept()
            request = client.recv(1024).decode('utf-8')
            request_path = request.split(' ')[1]

            status, response = handle_request(request_path)
            client.send(f'HTTP/1.1 {status}\r\n')
            client.send('Content-Type: text/html\r\n')
            client.send('Connection: close\r\n\r\n')
            if response:
                client.sendall(response.encode('utf-8'))
            client.close()

            moisture = read_moisture()
            temperature = read_temperature()

            data_interval = 5 if current_time - last_temperature_update <= 60 else 60
            if (current_time - last_temperature_update) % data_interval == 0:
                time_tuple = localtime()
                formatted_time = "{:02}:{:02}:{:02}".format(time_tuple[3], time_tuple[4], time_tuple[5])
                data_points.append({"time": formatted_time, "temperature": temperature, "moisture": moisture})
                if len(data_points) > 60:
                    data_points.pop(0)

            if not pump_state:
                if moisture < MOISTURE_THRESHOLD and not cooldown_active:
                    if not pump_state:
                        activate_pump()
                    elif pump_state and (current_time - last_pump_activation >= MAX_PUMP_TIME):
                        deactivate_pump()
                elif moisture >= MOISTURE_THRESHOLD:
                    if pump_state:
                        deactivate_pump()

            if cooldown_active and (current_time - last_pump_deactivation >= COOLDOWN_TIME):
                cooldown_active = False

            if not pump_state and pump_state and (current_time - last_pump_activation >= MAX_PUMP_TIME):
                deactivate_pump()

            if current_time - last_check_time > WATCHDOG_TIMEOUT:
                log("Software watchdog reset")
                machine.reset()
            last_check_time = current_time

            if current_time % 30 == 0:
                gc.collect()

            check_wifi_connection()

        except Exception as e:
            log(f'An error occurred: {e}')
            sleep(1)

# Run the main loop
main()
