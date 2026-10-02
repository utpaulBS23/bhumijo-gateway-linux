"""Hardware watchers: MC-38 reed switch (Pi GPIO) and exit button (KC868 input).

Both only observe. Nothing here can stop the door from opening.
"""

import logging

from relay import RelayError

log = logging.getLogger("inputs")


class ReedSwitch:
    """MC-38 on a Pi 5 GPIO via gpiozero + lgpio.

    Wiring: one leg to REED_GPIO, the other to GND, internal pull-up.
    Magnet present (door shut) closes the reed -> pin LOW -> "pressed".
    REED_ACTIVE_HIGH=1 flips this for NC-type sensors.
    """

    def __init__(self, settings, door):
        from gpiozero import Button   # lazy: only on the Pi
        self.door = door
        self.button = Button(settings.reed_gpio, pull_up=True, bounce_time=0.05)
        # Explicit lambdas: gpiozero may pass the device as the first argument
        opened, closed = (lambda: door.door_opened()), (lambda: door.door_closed())
        if settings.reed_active_high:
            self.button.when_pressed, self.button.when_released = opened, closed
            initially_open = self.button.is_pressed
        else:
            self.button.when_pressed, self.button.when_released = closed, opened
            initially_open = not self.button.is_pressed
        log.info("reed on GPIO%d, door initially %s",
                 settings.reed_gpio, "open" if initially_open else "closed")
        if initially_open:
            # Booted with the door open: track it so propped alerts still fire
            door.door_opened(at_boot=True)

    def close(self):
        self.button.close()


def run_exit_poller(settings, relay, door, stop):
    """Poll input_ctl.cgi and feed the exit-button state to the door."""
    warned = False
    backoff = settings.exit_poll_interval
    while not stop.is_set():
        try:
            inputs = relay.read_inputs()
            if inputs is None:
                if not warned:
                    log.error("relay firmware does not report inputs; "
                              "flash firmware/kc868/main.py (exit tracking off)")
                    warned = True
                stop.wait(30)
                continue
            door.exit_input(inputs[settings.exit_input_index])
            backoff = settings.exit_poll_interval
        except RelayError as e:
            log.warning("exit poll failed: %s", e)
            backoff = min(max(backoff * 2, 1.0), 30.0)
        stop.wait(backoff)


def run_door_ticker(door, stop, interval=1.0):
    while not stop.is_set():
        door.tick()
        stop.wait(interval)
