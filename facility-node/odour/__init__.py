"""Odour monitoring: air sensors, the TVOC score and the odour alert.

    sensors.py   ENS160 + AHT20 on the Pi's I2C or the ESP32 WiFi board; 1-minute push
    monitor.py   TVOC 0-100 score and the odour_high / odour_resolved alert
    mq.py        optional MQ-135 / MQ-136 gas sensors (ADS1115); calibrate / read CLI

Switched by ENABLE_ODOUR. Never in the unlock path: a dead sensor only means
the readings go out as nulls.
"""
