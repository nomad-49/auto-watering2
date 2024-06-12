# main.py
import time

def print_message():
    message = "Hello from the new main.py!"
    for i in range(5):
        print(message)
        time.sleep(1)

if __name__ == "__main__":
    print_message()
