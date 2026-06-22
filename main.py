import spidev
import time
import os
import requests
from dotenv import load_dotenv
from datetime import datetime
from w1thermsensor import W1ThermSensor


load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
SEND_INTERVAL = 30

spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 1350000

temp_sensor = W1ThermSensor()


def read_mcp3008(channel):
	adc = spi.xfer2([1, (8 + channel) << 4, 0])
	return ((adc[1] & 3) << 8) + adc[2]

def average_raw(channel, samples = 10):
	total = sum(read_mcp3008(channel) for _ in range(samples))
	return round(total/ samples, 1)

def main():
	print("=======================================")
	print(" 	NH3 MONITORING SYSTEM	      ")
	print("=======================================")
	print(f"Backend: {BACKEND_URL}")
	print(f"Interval: every {SEND_INTERVAL} seconds")
	print("=======================================")

	try:
		while True:
			payload = {
				"temperature": round(temp_sensor.get_temperature(), 2),
				"ph_raw": average_raw(channel=0),
				"nh3_raw": average_raw(channel=2),
				"timestampe": datatime.now().isoformat()
			}
			print(f"[SEND] {payload}")

			try:
				r = requests.post(BACKEND_URL, json=payload, timeout=5)
				print(f"[OK] STATUS: {r.status_code}")
			except requests.exceptions.ConnectionError:
				print("[ERROR] Cannot reach backend")
			except requests.exceptions.Timeout:
				print("[ERROR] Request timed out")

			time.sleep(SEND_INTERVAL)

	except KeyboardInterrupt:
		print("stopped")
	finally:
		spi.close()

if __name__ == "__main__":
	main()












































