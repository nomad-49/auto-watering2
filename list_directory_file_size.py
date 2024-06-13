import machine
import utime
import network
from time import sleep

# Set up the onboard LED
led = machine.Pin("LED", machine.Pin.OUT)

# Wi-Fi credentials
ssid = 'WAVLINK_A5C6'

# Add logging function
def log(message):
    with open("log.txt", "a") as log_file:
        log_file.write(f"{utime.localtime()}: {message}\n")

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid)  # No password needed for open Wi-Fi network
    
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

# Start of main code
log("Starting main.py")

try:
    # Activate LED to indicate start
    led.on()
    log("LED turned on")

    # Connect to Wi-Fi
    log("Connecting to Wi-Fi")
    connect_wifi()
    log("Wi-Fi connected")

    # Keep the LED on for a few seconds to indicate success
    sleep(10)
    
    # Log before rebooting
    log("Rebooting the system")
    led.off()  # Turn off LED before rebooting
    machine.reset()

except Exception as e:
    log(f"Error: {e}")
    led.off()  # Ensure LED is off in case of an error

