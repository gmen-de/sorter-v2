# SC15 Bluetooth bridge

Firmware for a classic ESP32 that sits next to Waveshare SC bus servos (SC09,
SC15, SC40, SC60) on a part that turns endlessly, so that only power has to
cross the slip ring. The servo data goes by Bluetooth.

The bridge owns the half-duplex servo bus. The Sorter's backend talks to it
through `hardware/bt_servo_bus.py` and sees an ordinary `ScServoBus`.

## What crosses the link

* **Bus transactions.** The backend sends one servo instruction packet; the
  bridge runs it on the bus at 1 Mbit/s and returns the status packet. The
  radio latency is paid once per command and never touches the bus timing.
* **Telemetry.** The bridge reads position, speed, load, voltage and
  temperature of the servos it was told to watch and streams them (25 Hz by
  default). `WaveshareServoMotor` uses them to tell that a move has arrived,
  is blocked, or timed out, instead of waiting a fixed time. The first time the
  backend asks for a servo it is added to the stream.
* **Heartbeat and watchdog.** The backend sends a heartbeat every 0.5 s. After
  1.5 s of silence the bridge notes the loss; with `release_on_loss` it also
  lets go of the watched servos. Without it they hold their last goal.

Frames are COBS-encoded with a CRC16 and a 0x00 delimiter. The frame types and
bodies are listed at the top of `sc15_bt_bridge.ino`. A request is repeated
with the same sequence number when its answer is lost; the bridge then answers
from memory instead of running the instruction twice.

## Hardware

* Waveshare Servo Driver with ESP32, or a classic ESP32 plus a bus servo
  adapter. The ESP32-S3 and -C3 have no Bluetooth SPP.
* Servo power into the board's DC jack at 6-8.4 V for the SC15 (it takes
  4.8-8.4 V). Check the board's and connector's current rating for your
  servos: a blocked SC15 draws about 1.7 A, a resting one about 0.2 A.
* `BUS_RX_PIN` / `BUS_TX_PIN` at the top of the sketch are the pins of the
  Waveshare board's bus circuit. Confirm them against the board's wiki.

## Flashing

Arduino IDE or `arduino-cli` with the ESP32 core, board "ESP32 Dev Module":

```bash
arduino-cli compile --fqbn esp32:esp32:esp32 software/firmware/sc15_bt_bridge
arduino-cli upload  --fqbn esp32:esp32:esp32 -p <port> software/firmware/sc15_bt_bridge
```

The serial monitor (115200) prints the device name when it is up.

## Pairing and connecting (Orange Pi, once)

```bash
bluetoothctl
power on
agent on
scan on          # wait for "SC15-Bridge", note its address
pair  AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
```

Then point the backend at the address in `machine.toml`:

```toml
[servo]
backend = "waveshare"
port = "bt:AA:BB:CC:DD:EE:FF"      # RFCOMM channel 1; "bt:...:FF/2" for another
```

If Python has no RFCOMM sockets, or you prefer a device node, bind one and use
`btserial:`:

```bash
sudo rfcomm bind /dev/rfcomm0 AA:BB:CC:DD:EE:FF 1
```

```toml
port = "btserial:/dev/rfcomm0"
```

The ESP32 takes one client at a time. The backend reconnects on its own when
the link drops; commands issued meanwhile fail at once with `ConnectionError`
rather than blocking.

## Tests

`software/sorter/backend/tests/test_bt_servo_bus.py` runs the host side against
a Python model of this firmware and of SC servos with real stops. It does not
exercise the firmware itself: flash a board and move one servo, without a load
on it, before trusting a flap to it.
