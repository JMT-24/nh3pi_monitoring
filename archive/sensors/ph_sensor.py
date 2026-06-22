import spidev
import time

spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 1350000


PH_CHANNEL = 0
VREF = 5.0
ADC_RESOLUTION = 1023.0

NEUTRAL_VOLTAGE = 2.549
SLOPE_FACTOR = 3.5

NUM_SAMPLES = 10
SAMPLE_DELAY = 0.01

def read_mcp3008(channel):
	if channel < 0 or channel > 7:
		raise ValueError("Channel must be 0-7")
	adc = spi.xfer2([1, (8 + channel) << 4, 0])
	raw = ((adc[1] &3) << 8) + adc[2]
	return raw

def read_ph():
	total = 0
	for _ in range(NUM_SAMPLES):
		total += read_mcp3008(PH_CHANNEL)
		time.sleep(SAMPLE_DELAY)

	avg_raw = total / NUM_SAMPLES
	voltage = avg_raw * (VREF/ ADC_RESOLUTION)

	ph_value = 7.0 + ((NEUTRAL_VOLTAGE - voltage) * SLOPE_FACTOR)
	ph_value = max(0.0, min(14.0, ph_value))
	return voltage, ph_value

def main():
	print("=========================")
	print(f"Neutral Voltage: {NEUTRAL_VOLTAGE} V")
	print(f"Slope Factor: {SLOPE_FACTOR}")
	print(f"Sample Reading: {NUM_SAMPLES}")
	print("=========================")

	try:
		while True:
			voltage, ph = read_ph()
			print(f"Voltage: {voltage:.3f} V | Calculated pH: {ph:.2f}")
			time.sleep(2)
	except KeyboardInterrupt:
		print("stopped")
	finally:
		spi.close()

if __name__ == "__main__":
	main()



























