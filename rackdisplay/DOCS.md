# Rack Display

Controls an SSD1306 128x32 OLED and a push button on a Raspberry Pi running Home Assistant OS.

## Behaviour

| Action | Result |
|---|---|
| Short press | Display shows hostname, IP, CPU % and MEM % for `display_timeout` seconds |
| Hold >= `reboot_hold_seconds` (5) | "Reboot" screen; the host reboots when you release the button |
| Hold >= `shutdown_hold_seconds` (10) | "Shutdown" screen; the host shuts down when you release the button |

Hostname and IP come from the Supervisor API (so they are the host's, not the container's).
Reboot/shutdown use the Supervisor API too (`hassio_role: manager`).

## Default wiring (BCM numbering)

| Part | Pin |
|---|---|
| OLED SDA / SCL | GPIO 2 / GPIO 3 (I2C bus 1, address `0x3C`) |
| Button | GPIO 20 to GND (active low, internal pull-up enabled) |
| LED | GPIO 23 (on while the add-on runs) |

## Prerequisite: enable I2C on Home Assistant OS

HAOS has I2C disabled by default and the add-on will not start without `/dev/i2c-1`.
Enable it with the "HAOS I2C Configurator" add-on (or add `dtparam=i2c_arm=on` to `config.txt` on the boot partition)
and reboot the host. Afterwards `/dev/i2c-1` shows up under Settings -> System -> Hardware.

## Options

| Option | Default | Description |
|---|---|---|
| `i2c_bus` | 1 | I2C bus number (`/dev/i2c-N`; the device mapping in `config.yaml` is for bus 1) |
| `i2c_address` | `0x3C` | OLED I2C address |
| `display_width` / `display_height` | 128 / 32 | Panel size (height 32 or 64) |
| `rotate_180` | true | Flip the picture 180 degrees. Toggle if the text is upside down |
| `contrast` | 255 | OLED contrast 0-255 (255 is the maximum) |
| `high_brightness` | true | Higher VCOMH level, noticeably brighter panel |
| `bold_text` | true | Draw text 2 px wide for better readability |
| `button_pin` | 20 | BCM pin of the button (0 disables) |
| `button_pull_up` | true | Enable the internal pull-up |
| `led_pin` | 23 | BCM pin of the LED (0 disables) |
| `gpio_chip` | 0 | `/dev/gpiochipN` |
| `display_timeout` | 15 | Seconds the display stays on after a press |
| `reboot_hold_seconds` | 5 | Hold time for the reboot screen |
| `shutdown_hold_seconds` | 10 | Hold time for the shutdown screen |
| `log_level` | info | debug / info / warning / error |
