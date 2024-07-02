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
ssid = 'VM5792329'
password = 'rk2dqJpcGyjd'

# URL of the raw GitHub file
url = "https://raw.githubusercontent.com/nomad-49/auto-watering2/main/list_directory_file_size.py"
local_file = "main.py"

class WiFiManager:
    def __init__(self, ssid, password):
        self.ssid = ssid
        self.password = password

    def connect_wifi(self, max_attempts=3, wait_time=20):
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        
        for attempt in range(1, max_attempts + 1):
            print(f'Connection attempt {attempt}')
            wlan.connect(self.ssid, self.password)
            
            attempt_wait_time = wait_time
            while attempt_wait_time > 0:
                if wlan.status() < 0 or wlan.status() >= 3:
                    break
                attempt_wait_time -= 1
                print('waiting for connection...')
                sleep(3)

            if wlan.status() == 3:
                print('connected')
                status = wlan.ifconfig()
                print('ip = ' + status[0])
                return status[0]
            else:
                print(f'Connection attempt {attempt} failed')

        raise RuntimeError('network connection failed')

    def reconnect(self):
        print("Attempting to reconnect to Wi-Fi...")
        ip = self.connect_wifi()
        return open_socket(ip)


class PumpController:
    def __init__(self, pump_pin, moisture_threshold, max_pump_time, cooldown_time):
        self.pump_pin = pump_pin
        self.moisture_threshold = moisture_threshold
        self.max_pump_time = max_pump_time
        self.cooldown_time = cooldown_time
        self.pump_state = False
        self.last_pump_activation = 0
        self.last_pump_deactivation = 0
        self.cooldown_active = False
        self.pump_log = []

    def activate_pump(self):
        try:
            if not self.pump_state:
                self.pump_pin.on()
                self.pump_state = True
                self.last_pump_activation = utime.time()
                self.pump_log.append(f"Pump Activated ({localtime_to_string(localtime())} for 0 seconds)")  # Initialize with 0 seconds
                if len(self.pump_log) > 10:  # Limit the pump log to 10 entries
                    self.pump_log.pop(0)
                print("Pump activated")
        except Exception as e:
            log(f"Error activating pump: {e}")

    def deactivate_pump(self):
        try:
            if self.pump_state:
                self.pump_pin.off()
                self.pump_state = False
                duration = int(utime.time() - self.last_pump_activation)
                self.pump_log[-1] = self.pump_log[-1].replace("0 seconds", f"{duration} seconds")
                self.cooldown_active = True  # Start cooldown period
                self.last_pump_deactivation = utime.time()
                print("Pump deactivated")
        except Exception as e:
            log(f"Error deactivating pump: {e}")

    def handle_pump_logic(self, moisture, pump_control_override):
        current_time = utime.time()
        if not pump_control_override:
            if moisture < self.moisture_threshold and not self.cooldown_active:
                if not self.pump_state:
                    self.activate_pump()
                elif self.pump_state and (current_time - self.last_pump_activation >= self.max_pump_time):
                    self.deactivate_pump()
            elif moisture >= self.moisture_threshold:
                if self.pump_state:
                    self.deactivate_pump()

        if self.cooldown_active and (current_time - self.last_pump_deactivation >= self.cooldown_time):
            self.cooldown_active = False

        if not pump_control_override and self.pump_state and (current_time - self.last_pump_activation >= self.max_pump_time):
            self.deactivate_pump()


class SensorManager:
    def __init__(self, moisture_pin, temp_sensor, conversion_factor, dry_value, wet_value):
        self.moisture_pin = moisture_pin
        self.temp_sensor = temp_sensor
        self.conversion_factor = conversion_factor
        self.dry_value = dry_value
        self.wet_value = wet_value

    def read_moisture(self):
        try:
            moisture_value = self.moisture_pin.read_u16()
            inverted_moisture = 65535 - moisture_value
            moisture_percentage = ((inverted_moisture - self.dry_value) / (self.wet_value - self.dry_value)) * 100
            moisture_percentage = max(0, min(moisture_percentage, 100))
            return moisture_percentage
        except Exception as e:
            log(f"Error reading moisture: {e}")
            return 0  # Return a default value in case of error

    def read_temperature(self):
        try:
            reading = self.temp_sensor.read_u16() * self.conversion_factor
            temperature = 27 - (reading - 0.706) / 0.001721
            return temperature
        except Exception as e:
            log(f"Error reading temperature: {e}")
            return 0  # Return a default value in case of error


class WebServer:
    def __init__(self, wifi_manager, pump_controller, sensor_manager):
        self.wifi_manager = wifi_manager
        self.pump_controller = pump_controller
        self.sensor_manager = sensor_manager
        self.data_points = []
        self.start_time = utime.time()
        self.last_check_time = utime.time()
        self.watchdog_timeout = 180  # 180-second timeout period
        self.led = machine.Pin("LED", machine.Pin.OUT)
        self.led_state = False
        self.led_override = False
        self.last_update_check = utime.time()
        self.last_temperature_update = utime.time()
        self.pump_control_override = False
        self.wlan = network.WLAN(network.STA_IF)
        self.update_message = ""

    def open_socket(self, ip):
        address = (ip, 80)
        connection = socket.socket()
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        connection.bind(address)
        connection.listen(1)
        return connection

    def handle_request(self, request_path):
        global moisture_threshold
        global led_override

        try:
            if request_path.startswith('/lighton'):
                self.control_led(True)
                return '200 OK', None
            if request_path.startswith('/lightoff'):
                self.control_led(False)
                return '200 OK', None
            if request_path.startswith('/pump?action=on'):
                self.pump_control_override = True
                self.pump_controller.activate_pump()
                return '200 OK', None
            if request_path.startswith('/pump?action=off'):
                self.pump_control_override = True
                self.pump_controller.deactivate_pump()
                return '200 OK', None
            if request_path.startswith('/autowater'):
                self.pump_control_override = False
                self.led_override = False  # Turn off LED override when autowater is enabled
                print("Autowater activated. Automatic control re-enabled.")
                return '200 OK', None
            if request_path.startswith('/threshold'):
                threshold_value = request_path.split('=')[1]
                if threshold_value and not threshold_value.isspace():
                    try:
                        self.pump_controller.moisture_threshold = float(threshold_value)
                        return '200 OK', None
                    except ValueError:
                        return '400 Bad Request', 'Invalid threshold value'
            if request_path.startswith('/data'):
                moisture = self.sensor_manager.read_moisture()
                current_time = utime.time()
                if current_time - self.last_temperature_update >= 30:
                    temperature = self.sensor_manager.read_temperature()
                    self.last_temperature_update = current_time
                else:
                    temperature = self.sensor_manager.read_temperature()
                response = '{{"temperature": {:.1f}, "moisture": {:.2f}}}'.format(temperature, moisture)
                return '200 OK', response
            if request_path.startswith('/pumplog'):
                pump_log_html = "".join([f"<p>{entry}</p>" for entry in self.pump_controller.pump_log])
                return '200 OK', pump_log_html
            if request_path.startswith('/update'):
                update_status = fetch_and_update()
                self.update_message = update_status
                response = json.dumps({"message": update_status})
                return '200 OK', response
        except Exception as e:
            log(f"Error handling request {request_path}: {e}")

        return '404 Not Found', '<h1>404 Not Found</h1>'

    def control_led(self, state):
        try:
            self.led_override = True
            self.led_state = state
            if state:
                self.led.on()
            else:
                self.led.off()
            log(f"LED {'on' if state else 'off'}")
        except Exception as e:
            log(f"Error controlling LED: {e}")

    def run(self):
        ip = self.wifi_manager.connect_wifi()
        connection = self.open_socket(ip)
        state = 'OFF'
        auto_water = False

        while True:
            try:
                current_time = utime.time()
                
                client, addr = connection.accept()
                request = client.recv(1024)
                request = request.decode('utf-8')
                request_path = request.split(' ')[1]

                if request_path != '/data' and request_path != '/pumplog':
                    print(f'Request Path: {request_path}')

                status, response = self.handle_request(request_path)
                moisture = self.sensor_manager.read_moisture()
                temperature = self.sensor_manager.read_temperature()

                if current_time - self.start_time <= 60:
                    data_interval = 5  # First 60 seconds: collect data every 5 seconds
                else:
                    data_interval = 60  # After 60 seconds: collect data every minute

                if (current_time - self.start_time) % data_interval == 0:
                    # Add new data point
                    time_tuple = localtime()
                    formatted_time = "{:02}:{:02}:{:02}".format(time_tuple[3], time_tuple[4], time_tuple[5])
                    self.data_points.append({"time": formatted_time, "temperature": temperature, "moisture": moisture})
                    if len(self.data_points) > 60:  # Keep only the last 60 data points
                        self.data_points.pop(0)

                self.pump_controller.handle_pump_logic(moisture, self.pump_control_override)

                if request_path == '/data' or request_path == '/pumplog':
                    client.send(f'HTTP/1.1 {status}\r\n')
                    client.send('Content-Type: text/html\r\n')
                    client.send('Connection: close\r\n\r\n')
                    client.sendall(response.encode('utf-8'))
                else:
                    response = webpage(temperature, state, moisture, auto_water, self.data_points, self.pump_controller.moisture_threshold, self.update_message)
                    client.send(f'HTTP/1.1 {status}\r\n')
                    client.send('Content-Type: text/html\r\n')
                    client.send('Connection: close\r\n\r\n')
                    client.sendall(response.encode('utf-8'))
                client.close()

                if not self.led_override:
                    self.led_state = not self.led_state
                    self.led.value(self.led_state)
                    utime.sleep(0.5)

                if current_time - self.last_check_time > self.watchdog_timeout:
                    print("Software watchdog reset")
                    machine.reset()
                self.last_check_time = current_time

                if current_time % 30 == 0:  # Run garbage collection every 30 seconds
                    gc.collect()

                if not self.wlan.isconnected():
                    connection = self.wifi_manager.reconnect()

            except Exception as e:
                print(f'An error occurred: {e}')
                if not self.wlan.isconnected():
                    connection = self.wifi_manager.reconnect()
                else:
                    sleep(1)  # Small delay before continuing

# Auxiliary functions
def log(message):
    print(message)  # Print to Thonny's console for visibility

def localtime_to_string(time_tuple):
    return "{:02}/{:02}/{} at {:02}:{:02}:{:02}".format(time_tuple[2], time_tuple[1], time_tuple[0], time_tuple[3], time_tuple[4], time_tuple[5])

def fetch_and_update():
    temp_file = "temp_main.py"
    try:
        log("Checking for updates.")
        gc.collect()  # Force garbage collection to free up memory
        response = urequests.get(url)
        if response.status_code == 200:
            log("Successfully fetched the remote file.")
            # Open a temporary file to write the downloaded content
            with open(temp_file, 'wb') as f:
                while True:
                    chunk = response.raw.read(1024)  # Read in smaller chunks
                    if not chunk:
                        break
                    f.write(chunk)
            response.close()

            # Compare the downloaded file with the current file
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
                uos.remove(temp_file)  # Remove the temporary file before reset
                machine.reset()  # Restart the device to run the updated code
            else:
                log("No updates found. Local file is up to date.")
                return "No new software available"
        else:
            log(f"Failed to fetch the file. Status code: {response.status_code}")
            return "Failed to fetch the update"
    except Exception as e:
        log(f"Error fetching or updating the file: {str(e)}")
        return f"Error: {str(e)}"
    finally:
        # Ensure the temporary file is deleted
        try:
            uos.remove(temp_file)
        except OSError:
            pass
        gc.collect()  # Force garbage collection to free up memory

def webpage(temperature, state, moisture, auto_water, data_points, threshold, update_message=""):
    auto_water_status = "ON" if not auto_water else "OFF"
    data_json = json.dumps(data_points[-60:])  # Keep only the last 60 data points
    led_color = "lightgreen" if state == "ON" else "darkgrey"
    temperature_color = "black"
    if temperature > 30:
        temperature_color = "#ff6666"  # pastel red
    elif temperature < 5:
        temperature_color = "#6666ff"  # pastel blue

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
                            temperatureElement.style.color = '#ff6666'; // pastel red
                        }} else if (data.temperature < 5) {{
                            temperatureElement.style.color = '#6666ff'; // pastel blue
                        }} else {{
                            temperatureElement.style.color = 'black';
                        }}
                        document.getElementById('moisture').innerHTML = data.moisture + '%';
                        // Add new data point
                        var currentTime = new Date().toLocaleTimeString('en-GB', {{ hour12: false }});
                        dataPoints.push({{time: currentTime, temperature: data.temperature, moisture: data.moisture}});
                        // Keep only the last 60 data points
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
                                borderColor: 'rgba(255, 99, 132, 0.6)', // Pastel red
                                backgroundColor: 'rgba(255, 99, 132, 0.2)', // Pastel red fill
                                borderWidth: 1,
                                fill: false,
                                pointStyle: 'circle'
                            }},
                            {{
                                label: 'Moisture (%)',
                                data: dataPoints.map(dp => dp.moisture),
                                borderColor: 'rgba(54, 162, 235, 0.6)', // Pastel blue
                                backgroundColor: 'rgba(54, 162, 235, 0.2)', // Pastel blue fill
                                borderWidth: 1,
                                fill: false,
                                pointStyle: 'circle'
                            }},
                            {{
                                label: 'Pump Threshold',
                                data: dataPoints.map(dp => moistureThreshold),
                                borderColor: 'rgba(75, 75, 75, 1)', // Dark grey
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
                initializeChart(); // Initialize chart on page load
                refreshData(); // Load initial data
                refreshPumpLog(); // Load initial pump log
                setInterval(refreshData, 5000); // Refresh data every 5 seconds
                setInterval(refreshPumpLog, 5000); // Refresh pump log every 5 seconds
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

# Initialize components
moisture_pin = ADC(26)
pump_pin = Pin(16, Pin.OUT)
temp_sensor = ADC(4)
conversion_factor = 3.3 / (65535)
dry_value = 43000
wet_value = 50000
moisture_threshold = 30.0
max_pump_time = 60  # 60 seconds
cooldown_time = 30  # 30 seconds

# Instantiate classes
wifi_manager = WiFiManager(ssid, password)
pump_controller = PumpController(pump_pin, moisture_threshold, max_pump_time, cooldown_time)
sensor_manager = SensorManager(moisture_pin, temp_sensor, conversion_factor, dry_value, wet_value)
web_server = WebServer(wifi_manager, pump_controller, sensor_manager)

# Run the web server
web_server.run()
