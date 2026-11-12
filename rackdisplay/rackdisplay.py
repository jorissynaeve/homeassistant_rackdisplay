#!/usr/bin/env python3
"""Rack display: SSD1306 OLED info screen + button for Home Assistant OS.

Port of the old Raspberry Pi ``infoscreen.py``:
  * short press  -> show hostname / IP / CPU / MEM for ``display_timeout`` seconds
  * hold >= 5 s  -> "Reboot" screen, reboot on release
  * hold >= 10 s -> "Shutdown" screen, shutdown on release
The host is rebooted / shut down through the Supervisor API.
"""
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.request

import psutil
from PIL import Image, ImageDraw, ImageFont

LOG = logging.getLogger("rackdisplay")
OPTIONS_FILE = "/data/options.json"
SUPERVISOR = "http://supervisor"
POLL_INTERVAL = 0.1  # button polling / redraw tick (s)
INFO_REFRESH = 10.0  # hostname / IP refresh (s)

stop_event = threading.Event()


# --------------------------------------------------------------------------- config
def load_options():
    with open(OPTIONS_FILE, encoding="utf-8") as fh:
        opts = json.load(fh)
    opts["i2c_address"] = int(str(opts["i2c_address"]), 0)
    return opts


# --------------------------------------------------------------------------- display
class SSD1306:
    """Minimal SSD1306 I2C driver (page/horizontal addressing, SMBus)."""

    def __init__(self, bus, address, width, height, rotate_180):
        from smbus import SMBus

        self.bus = SMBus(bus)
        self.addr = address
        self.width = width
        self.height = height
        self.pages = height // 8
        com_pins = 0x02 if height == 32 else 0x12
        flip = 0 if rotate_180 else 1  # 1 = Adafruit "rotation 0" (A1/C8), 0 = 180 deg
        init = [
            0xAE,  # display off
            0x20, 0x00,  # horizontal addressing
            0x40,  # start line 0
            0xA0 | flip,  # segment remap
            0xA8, height - 1,  # multiplex ratio
            0xC0 | (flip << 3),  # COM scan direction
            0xD3, 0x00,  # display offset
            0xDA, com_pins,
            0xD5, 0x80,  # clock divide
            0xD9, 0xF1,  # pre-charge
            0xDB, 0x30,  # vcomh
            0x81, 0xFF,  # contrast
            0xA4,  # follow RAM
            0xA6,  # normal (not inverted)
            0x8D, 0x14,  # charge pump on
            0xAF,  # display on
        ]
        for c in init:
            self._cmd(c)

    def _cmd(self, c):
        self.bus.write_byte_data(self.addr, 0x00, c)

    def show(self, image):
        """Push a mode '1' PIL image of the display size to the panel."""
        px = image.load()
        data = []
        for page in range(self.pages):
            for x in range(self.width):
                byte = 0
                for bit in range(8):
                    if px[x, page * 8 + bit]:
                        byte |= 1 << bit
                data.append(byte)
        self._cmd(0x21); self._cmd(0); self._cmd(self.width - 1)
        self._cmd(0x22); self._cmd(0); self._cmd(self.pages - 1)
        for i in range(0, len(data), 32):  # SMBus block limit is 32 bytes
            self.bus.write_i2c_block_data(self.addr, 0x40, data[i:i + 32])

    def power(self, on):
        self._cmd(0xAF if on else 0xAE)

    def close(self):
        try:
            self.power(False)
            self.bus.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- GPIO (libgpiod v1 python bindings)
class Gpio:
    def __init__(self, chip, button_pin, pull_up, led_pin):
        import gpiod

        self.gpiod = gpiod
        self.chip = gpiod.Chip(f"gpiochip{chip}")
        self.button = self.led = None
        if button_pin:
            self.button = self.chip.get_line(button_pin)
            flags = gpiod.LINE_REQ_FLAG_BIAS_PULL_UP if pull_up else 0
            try:
                self.button.request(consumer="rackdisplay", type=gpiod.LINE_REQ_DIR_IN, flags=flags)
            except OSError:
                LOG.warning("Could not set pull-up bias, requesting plain input")
                self.button.release()
                self.button.request(consumer="rackdisplay", type=gpiod.LINE_REQ_DIR_IN)
        if led_pin:
            self.led = self.chip.get_line(led_pin)
            self.led.request(consumer="rackdisplay", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[1])

    def pressed(self):
        """Button is active low (0 == pressed), as on the old Pi."""
        return self.button is not None and self.button.get_value() == 0

    def close(self):
        for line in (self.led, self.button):
            if line is not None:
                try:
                    line.release()
                except OSError:
                    pass
        self.chip.close()


# --------------------------------------------------------------------------- Supervisor API
def supervisor(path, method="GET"):
    req = urllib.request.Request(
        SUPERVISOR + path,
        method=method,
        headers={"Authorization": "Bearer " + os.environ.get("SUPERVISOR_TOKEN", "")},
        data=b"" if method == "POST" else None,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read()
    return json.loads(body).get("data", {}) if body else {}


def host_info():
    """Return (hostname, ip) of the *host* (not the container)."""
    hostname, ip = "?", "?"
    try:
        hostname = supervisor("/host/info").get("hostname") or hostname
    except Exception as err:  # noqa: BLE001
        LOG.warning("host/info failed: %s", err)
    try:
        ifaces = supervisor("/network/info").get("interfaces", [])
        ifaces.sort(key=lambda i: (not i.get("primary"), not i.get("connected")))
        for iface in ifaces:
            addrs = (iface.get("ipv4") or {}).get("address") or []
            if addrs:
                ip = addrs[0].split("/")[0]
                break
    except Exception as err:  # noqa: BLE001
        LOG.warning("network/info failed: %s", err)
    return hostname, ip


# --------------------------------------------------------------------------- main
def main():
    opts = load_options()
    logging.basicConfig(
        level=getattr(logging, opts["log_level"].upper()),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    LOG.info("Starting rack display (options: %s)", {k: v for k, v in opts.items() if k != "log_level"})

    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())

    width, height = opts["display_width"], opts["display_height"]
    disp = SSD1306(opts["i2c_bus"], opts["i2c_address"], width, height, opts["rotate_180"])
    gpio = Gpio(opts["gpio_chip"], opts["button_pin"], opts["button_pull_up"], opts["led_pin"])

    image = Image.new("1", (width, height))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    top, line_h = -2, 12

    shown = {"lines": None}

    def screen(*lines):
        if shown["lines"] == lines:  # nothing changed, spare the I2C bus
            return
        shown["lines"] = lines
        draw.rectangle((0, 0, width, height), outline=0, fill=0)
        for i, text in enumerate(lines):
            draw.text((0, top + i * line_h), text, font=font, fill=255)
        disp.show(image)

    screen("--------------------", " Infoscreen Started ", "--------------------")
    stop_event.wait(5)

    hostname, ip = host_info()
    last_info = time.monotonic()
    last_stats, stats = 0.0, ""
    display_until = 0.0
    held_since = None
    menu_state = 0  # 0 info, 1 reboot, 2 shutdown
    display_on = False
    action = None

    while not stop_event.is_set():
        now = time.monotonic()

        if gpio.pressed():
            if held_since is None:
                held_since = now
            held = now - held_since
            menu_state = 2 if held >= opts["shutdown_hold_seconds"] else 1 if held >= opts["reboot_hold_seconds"] else 0
            display_until = now + opts["display_timeout"]
        elif held_since is not None:  # released
            if menu_state == 1:
                action = "reboot"
            elif menu_state == 2:
                action = "shutdown"
            held_since = None
            menu_state = 0

        if action:
            screen("", "Performing Reboot..." if action == "reboot" else "Shutting down.......")
            LOG.warning("Button action: %s", action)
            try:
                supervisor(f"/host/{action}", "POST")
            except Exception as err:  # noqa: BLE001
                LOG.error("Supervisor %s failed: %s", action, err)
                screen("", "  %s failed!  " % action)
            stop_event.wait(3)
            action = None
            display_until = 0.0
            continue

        if now < display_until:
            display_on = True
            if menu_state == 0:
                if now - last_info > INFO_REFRESH:
                    hostname, ip = host_info()
                    last_info = now
                if now - last_stats >= 1.0:  # CPU/MEM refresh once per second
                    stats = "CPU : {:3.0f}% | MEM: {:2.0f}%".format(psutil.cpu_percent(), psutil.virtual_memory().percent)
                    last_stats = now
                screen("NAME: " + hostname, "IP  : " + ip, stats)
            elif menu_state == 1:
                screen(".......Reboot.......", "   Release Button   ", "      To Reboot     ")
            else:
                screen("......Shutdown......", "   Release Button   ", "    To Shutdown     ")
        elif display_on:
            display_on = False
            draw.rectangle((0, 0, width, height), outline=0, fill=0)
            shown["lines"] = None
            disp.show(image)  # blank screen when the timeout expired

        stop_event.wait(POLL_INTERVAL)

    LOG.info("Stopping")
    disp.close()
    gpio.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOG.exception("Fatal error")
        sys.exit(1)
