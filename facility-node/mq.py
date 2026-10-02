#!/usr/bin/env python3
"""MQ-135 (ammonia) and MQ-136 (H2S) gas sensors through an ADS1115 ADC.

The Pi has no analog inputs, so the MQ modules' analog output (AOUT) goes into
an ADS1115 on the same I2C bus (address 0x48).

Wiring (per MQ module):
  MQ VCC -> 5V   (the heater needs 5V)     MQ GND -> GND
  MQ AOUT -> voltage divider -> ADS1115 A0 (MQ-135) / A1 (MQ-136)
  ADS1115 VDD -> 3.3V (NOT 5V: the Pi's I2C is 3.3V), ADDR -> GND (0x48)

AOUT swings up to 5V, which is above the ADS1115's 3.3V supply, so use a
divider: 10k from AOUT to the ADS pin, 20k from the ADS pin to GND. That
scales 5V to 3.33V; set MQ_DIVIDER=1.5 (= (10k+20k)/20k).

ppm needs a clean-air calibration (R0) per sensor. After 24-48h burn-in, in
fresh air, run on the Pi:

    sudo -u facility ./venv/bin/python mq.py calibrate

and paste the printed MQ135_R0 / MQ136_R0 lines into .env. Without R0 the
node sends nh3_ppm/h2s_ppm as null.

The ppm curves are datasheet fits: good for trends and thresholds, not lab
measurements.
"""

import argparse
import logging
import time

from config import load_env_file

log = logging.getLogger("mq")

ADS1115_CONVERSION = 0x00
ADS1115_CONFIG = 0x01
ADS1115_FSR = 4.096   # volts, PGA setting below

# ppm = A * (Rs/R0) ** B, fitted from the datasheet sensitivity curves
CURVES = {
    "mq135_nh3": (102.2, -2.473),
    "mq136_h2s": (36.737, -3.536),
}


# ---- pure maths (unit-tested) --------------------------------------------

def ads1115_config(channel):
    """Single-shot, single-ended AINx vs GND, +/-4.096V, 128 SPS, comparator off."""
    if not 0 <= channel <= 3:
        raise ValueError(f"ADS1115 channel {channel} not in 0-3")
    return (0x8000                     # OS: start a conversion
            | ((0b100 + channel) << 12)  # MUX: AINx vs GND
            | (0b001 << 9)             # PGA: +/-4.096V
            | (1 << 8)                 # MODE: single-shot
            | (0b100 << 5)             # DR: 128 SPS
            | 0b11)                    # COMP_QUE: disabled


def ads1115_volts(msb, lsb):
    raw = (msb << 8) | lsb
    if raw & 0x8000:
        raw -= 1 << 16
    return raw * ADS1115_FSR / 32768


def mq_rs(v_out, vc, rl_kohm):
    """Sensor resistance (kOhm) from the module's output voltage."""
    if v_out <= 0:
        return None
    return rl_kohm * (vc - v_out) / v_out


def mq_ppm(rs, r0, curve):
    if rs is None or not r0:
        return None
    a, b = CURVES[curve]
    return a * (rs / r0) ** b


# ---- hardware ------------------------------------------------------------

class ADS1115:
    def __init__(self, address=0x48, bus_no=1):
        from smbus2 import SMBus   # lazy: absent on dev machines
        self.address = address
        self.bus = SMBus(bus_no)

    def volts(self, channel):
        cfg = ads1115_config(channel)
        self.bus.write_i2c_block_data(self.address, ADS1115_CONFIG, [cfg >> 8, cfg & 0xFF])
        time.sleep(0.01)   # 128 SPS -> ~8 ms per conversion
        msb, lsb = self.bus.read_i2c_block_data(self.address, ADS1115_CONVERSION, 2)
        return ads1115_volts(msb, lsb)


class MQSensors:
    """Reads both MQ sensors; channels set to None are skipped."""

    def __init__(self, settings, adc=None, samples=5):
        self.s = settings
        self.adc = adc
        self.samples = samples
        self.sensors = [(key, ch, r0, curve) for key, ch, r0, curve in (
            ("nh3_ppm", settings.mq135_channel, settings.mq135_r0, "mq135_nh3"),
            ("h2s_ppm", settings.mq136_channel, settings.mq136_r0, "mq136_h2s"),
        ) if ch is not None]

    def _adc(self):
        if self.adc is None:
            self.adc = ADS1115(self.s.ads1115_addr)
        return self.adc

    def rs(self, channel):
        """Averaged sensor resistance (kOhm) on one ADS channel."""
        adc = self._adc()
        v = sum(adc.volts(channel) for _ in range(self.samples)) / self.samples
        return mq_rs(v * self.s.mq_divider, self.s.mq_vc, self.s.mq_rl_kohm)

    def read(self):
        out = {key: None for key, *_ in self.sensors}
        for key, ch, r0, curve in self.sensors:
            try:
                ppm = mq_ppm(self.rs(ch), r0, curve)
                out[key] = round(ppm, 2) if ppm is not None else None
            except Exception as e:
                self.adc = None   # reopen next time
                log.error("%s read failed: %s", key, e)
        return out


# ---- calibration CLI -----------------------------------------------------

def _num(env, key, default, cast=float):
    v = env.get(key, "")
    return cast(v) if v and v.lower() != "none" else default


def settings_view(env):
    """Minimal settings object for MQSensors, from a parsed .env."""
    class S:
        ads1115_addr = int(env.get("ADS1115_ADDR", "0x48"), 0)
        mq135_channel = _num(env, "MQ135_CHANNEL", 0, int)
        mq136_channel = _num(env, "MQ136_CHANNEL", 1, int)
        mq135_r0 = _num(env, "MQ135_R0", None)
        mq136_r0 = _num(env, "MQ136_R0", None)
        mq_vc = _num(env, "MQ_VC", 5.0)
        mq_rl_kohm = _num(env, "MQ_RL_KOHM", 10.0)
        mq_divider = _num(env, "MQ_DIVIDER", 1.5)
    return S


def calibrate(env, seconds, interval=2.0, adc=None):
    """Average Rs in clean air -> R0 = Rs / clean-air ratio, per sensor."""
    ratios = {"nh3_ppm": ("MQ135_R0", _num(env, "MQ135_CLEAN_RATIO", 3.6)),
              "h2s_ppm": ("MQ136_R0", _num(env, "MQ136_CLEAN_RATIO", 3.6))}
    mq = MQSensors(settings_view(env), adc=adc)
    samples = {key: [] for key, *_ in mq.sensors}
    end = time.time() + seconds
    while True:
        for key, ch, _, _ in mq.sensors:
            rs = mq.rs(ch)
            if rs is not None:
                samples[key].append(rs)
        if time.time() >= end:
            break
        time.sleep(interval)
    result = {}
    for key, values in samples.items():
        if values:
            name, ratio = ratios[key]
            result[name] = round(sum(values) / len(values) / ratio, 3)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description="MQ sensor tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calibrate", help="measure R0 in clean air")
    c.add_argument("--env", default="/opt/facility-node/.env")
    c.add_argument("--minutes", type=float, default=3)
    sub.add_parser("read", help="print current Rs and ppm").add_argument(
        "--env", default="/opt/facility-node/.env")
    args = p.parse_args(argv)
    env = load_env_file(args.env)

    if args.cmd == "calibrate":
        print(f"Sampling clean air for {args.minutes} min. Keep the sensors in fresh "
              "air, away from people and cleaning products...")
        result = calibrate(env, args.minutes * 60)
        if not result:
            raise SystemExit("no valid readings: check ADS1115 wiring (i2cdetect -y 1 -> 48)")
        print("\nPaste into .env, then: sudo systemctl restart facility-node\n")
        for k, v in result.items():
            print(f"{k}={v}")
    else:
        mq = MQSensors(settings_view(env))
        for key, ch, r0, _ in mq.sensors:
            rs = mq.rs(ch)
            shown = f"{rs:.2f} kOhm" if rs is not None else "no signal (0 V)"
            print(f"{key}: channel A{ch}, Rs={shown}, R0={r0}")
        print(mq.read())


if __name__ == "__main__":
    main()
