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

import pumpControl

# --------------- Toolhead control --------------

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

# ----------------Pump Control-----------------------------

def initializePump(COMPort, syringeVol, syringeDiam):
    pumpChain = pumpControl.Chain(f'{COMPort}')
    pump = pumpControl.Pump(pumpChain)
    pump.cvolume()
    pump.setdiameter(syringeDiam) # diameter in mm
    pump.setsyringevolume(syringeVol, "m") # see pumpy documentation for unit labels. m = mL

    return pump

def withdraw(pump, withdrawVol, flowRate=1, volUnits='m', flowRateUnits="m/s"):
    # see pumpControl.py or pumpy documentation for flowrate units. Units of [volume]/[time] are listed in pumpy as [m,u,p]/[h,m,s] and correspond to [mL, uL, pL]/[hour, min, sec]
    pump.setwithdrawrate(flowRate, flowRateUnits)  # m/m = ml/sec

    # pump.infuseDuration(steadyStateDevelopment)
    calibratedWithdrawVol = withdrawVol + .0344 #(.85*withdrawVol-25.413)/1000
    pump.withdrawDuration(calibratedWithdrawVol/flowRate)


def infuse(pump, infuseVol, flowRate=1, volUnits='m', flowRateUnits="m/s"):
    # see pumpControl.py or pumpy documentation for flowrate units. Units of [volume]/[time] are listed in pumpy as [m,u,p]/[h,m,s] and correspond to [mL, uL, pL]/[hour, min, sec]
    pump.setinfusionrate(flowRate, flowRateUnits)  # m/m = ml/sec

    # pump.infuseDuration(steadyStateDevelopment)
    calibratedInfuseVol = infuseVol + .0344 #(.85 * infuseVol - 25.413) / 1000
    pump.infuseDuration(calibratedInfuseVol/flowRate)

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


