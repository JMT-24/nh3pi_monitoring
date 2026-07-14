import spidev
import time

spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 1350000

NH3_CHANNEL = 2
VREF = 5.0
ADC_RESOLUTION = 1023.0

NUM_SAMPLES = 10
SAMPLE_DELAY = 0.1

RL = 10.0
RO = 30.0
A = 102.7
B = -2.473

WARMUP_SECONDS = 65

def read_mcp3008(channel):
	if channel < 0 or channel > 7:
		raise ValueError("Channel must be 0-7")
	adc = spi.xfer2([1, (8 + channel) << 4, 0])
	raw = ((adc[1] & 3) << 8) + adc[2]
	return raw

def read_nh3():
	total = 0
	ratio = 0
	for _ in range(NUM_SAMPLES):
		total += read_mcp3008(NH3_CHANNEL)
		time.sleep(SAMPLE_DELAY)

	avg_raw = total / NUM_SAMPLES
	voltage = avg_raw * (VREF / ADC_RESOLUTION)

	if voltage == 0:
		return avg_raw, voltage, None, None

	rs = ((VREF - voltage) / voltage) * RL
	# Was `ratio * rs / RO` — an expression whose result was discarded, so `ratio`
	# stayed 0 and `A * (0 ** -2.473)` raised ZeroDivisionError on every read.
	ratio = rs / RO
	if ratio <= 0:
		return avg_raw, voltage, rs, None

	ppm = A * (ratio ** B)
	ppm = max(0.0, ppm)

	return avg_raw, voltage, rs, ppm


def warmup():
	print(f"Warming up... wait {WARMUP_SECONDS} seconds")
	for i in range(WARMUP_SECONDS, 0, -1):
		print(f"Warm-up: {i} seconds remaining...", end="\r")
		time.sleep(1)
	print("\nReady!")
	print("=====================================\n")

def main():
	print("======================================")
	print(f"Channel : CH{NH3_CHANNEL}")
	print(f"RL : {RL} k ohms")
	print(f"RO : {RO} K ohms")
	print(f"Curve : {A} * (Rs/Ro) ^ {B}")
	print("======================================")

	warmup()

	try:
		while True:
			avg_raw, voltage, rs, ppm = read_nh3()

			if rs is None:
				print("Voltage: 0.0000 V | Sensor not connected or no signal")
			elif ppm is None:
				print(f"Raw: {avg_raw:6.1f} | Voltage: {voltage:.3f} V | Rs: {rs:.2f} k | NH3: --")
			else:
				print(
					f"Raw: {avg_raw:6.1f} | "
					f"Voltage: {voltage:.3f} V | "
					f"Rs: {rs:.2f} k | "
					f"NH3: {ppm:.2f} ppm"
				)

			time.sleep(2)  # was 2000 (33 minutes) — almost certainly a typo for 2.0

	except KeyboardInterrupt:
		print("stopped")

	finally:
		spi.close()


if __name__ == "__main__":
	main()


































































