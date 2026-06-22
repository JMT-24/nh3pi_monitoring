from w1thermsensor import W1ThermSensor, SensorNotReadyError

def read_temperature():
	try:
		sensor = W1ThermSensor()
		temp = sensor.get_temperature()
		return round(temp, 2)
	except SensorNotReadyError:
		print("Sensor not ready yet, try again")
		return None
	except Exception as e:
		print(f"Error reading temp: {e}")
		return None

if __name__ == "__main__":
	print("Reading DS18B20 temperature sensor...")
	temp = read_temperature()
	if temp is not None:
		print(f"Temperature: {temp} C")

