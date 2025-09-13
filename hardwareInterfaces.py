# dosing_runner.py
# Orchestrates dosing: per-liquid, per-well, using placeholder hardware calls.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import json
import time

import serial
import time
from datetime import datetime
import numpy as np



def command(ser, cmd):
    """Send G-code command and wait for 'ok' response."""
    ser.write((cmd.strip() + "\r\n").encode())
    time.sleep(0.1)

    while True:
        line = ser.readline().decode("utf-8", errors="ignore").strip()
        if line:
            print(f"< {line}")  # echo printer responses
        if line.startswith("ok"):
            break


def moveTo(ser, x, y, z, zlift=30, speed=12000):
    # lift so head is out of the way, then move to x, y coordinates, then move to z
    ZLift = f"G1 Z{zlift:.2f} F{speed}"
    XY = f"G1 X{x:.2f} Y{y:.2f} F{speed}"
    ZFinal = f"G1 Z{z:.2f} F{speed}"

    # lift print head to avoid knocking materials on bed
    command(ser, ZLift)
    command(ser, "M400")

    # move to XY position
    command(ser, XY)
    command(ser, "M400")

    # move to final z position
    command(ser, ZFinal)
    command(ser, "M400")

    return None

def dosePositioning(ser, z=0, speed=9000):
    # lowers to dosing z position without changing x or y position. Default is zero

    gcode = f"G1 Z{z:.2f} F{speed}"
    command(ser, gcode)
    command(ser, "M400")

    return None

# --------------------------------------------------------------------
# ser = serial.Serial("COM5", 115200, timeout=2)
# time.sleep(2)
#
# command(ser, "G28")   # home all axes
# command(ser, "G21")   # mm mode
# command(ser, "G90")   # absolute positioning
# command(ser, "M203 Z1200") # adjust z direction max speed



yVals = np.linspace(81.5, 81.5 + 9 * 7, 8)  # 8 steps in X
xVals = np.linspace(63.5, 63.5 + 9 * 11, 12)  # 12 steps in Y
solutionA = [37.66, 196]


# example script using the above to dose 1 fluid
# try:
#     for i in xVals:
#         for j in yVals:
#             # reservior location on the wellplate
#             moveTo(ser, 37.66, 196, 0)
#             dosePositioning(ser)
#             moveTo(ser, i, j, 0)
#
# except KeyboardInterrupt:
#     print("Stopped by user")
#
# finally:
#     ser.close()


# ender:
# location calibration
# move(x,y,z)
# swirly motion
#
# syringe pump
# withdraw
# infuse


