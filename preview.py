# OpenMV H7 + FLIR Lepton IDE preview script.
#
# Use this from OpenMV IDE when you need live video for aiming/setup.
# It does not record and does not listen for commands.

import sensor
import time


FRAME_SIZE = sensor.QQVGA
PIX_FORMAT = sensor.GRAYSCALE


print("Resetting Lepton...")
sensor.reset()

print(
    "Lepton Res (%dx%d)"
    % (
        sensor.ioctl(sensor.IOCTL_LEPTON_GET_WIDTH),
        sensor.ioctl(sensor.IOCTL_LEPTON_GET_HEIGHT),
    )
)
print(
    "Radiometry Available: "
    + ("Yes" if sensor.ioctl(sensor.IOCTL_LEPTON_GET_RADIOMETRY) else "No")
)
try:
    print("Lepton Refresh: %s Hz" % sensor.ioctl(sensor.IOCTL_LEPTON_GET_REFRESH))
except Exception:
    pass

sensor.set_pixformat(PIX_FORMAT)
sensor.set_framesize(FRAME_SIZE)
sensor.skip_frames(time=5000)

clock = time.clock()
while True:
    clock.tick()
    sensor.snapshot()
    print(clock.fps())
