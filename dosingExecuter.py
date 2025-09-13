from __future__ import annotations

import sys
import time
import json
import types
import argparse
from collections import defaultdict
from typing import Dict, List, Tuple, Any

# External modules you already use
import serial

# Your modules (keep filenames as doserGUI.py and hardwareInterfaces.py)
import doserGUI as gui
import hardwareInterfaces as hw


# -------------------- Configuration --------------------

# Plate coordinate configs: A1 origin and pitch for each plate preset name.
# Update these with your calibrated coordinates.
PLATE_OFFSETS: Dict[str, Dict[str, float]] = {
    # Example defaults from your movement script
    "96-well (8x12)": {
        "a1_x": 63.5,
        "a1_y": 81.5,
        "pitch_x": 9.0,
        "pitch_y": 9.0,
    },

    # Provide your calibrated values for other plate types if you use them:
    "384-well (16x24)": {
        "a1_x": 63.5,     # TODO: update with your 384-well A1 origin
        "a1_y": 81.5,     # TODO: update with your 384-well A1 origin
        "pitch_x": 4.5,
        "pitch_y": 4.5,
    },
    "24-well (4x6)": {
        "a1_x": 63.5,     # TODO: update with your 24-well A1 origin
        "a1_y": 81.5,     # TODO: update with your 24-well A1 origin
        "pitch_x": 19.3,
        "pitch_y": 19.3,
    },
}

# Reservoir (source) coordinates per fluid label.
# Provide the XY for every fluid label you’ll actually dose.
FLUID_RESERVOIRS: Dict[str, Tuple[float, float]] = {
    "A": (37.66, 196.00),
    # "B": (xB, yB),
    # "C": (xC, yC),
    # "D": (xD, yD),
    # "E": (xE, yE),
    # "F": (xF, yF),
}

# Movement heights and speeds (mm and mm/min)
TRAVEL_Z = 30.0     # safe travel Z for XY moves
DOSE_Z = 0.0        # approach Z for dispensing in wells
ASPIRATE_Z = 0.0    # approach Z in reservoir for aspiration
F_XY = 12000        # XY feed
F_Z = 9000          # Z feed


# -------------------- Helpers --------------------

def well_to_xy(row: int, col: int, a1_x: float, a1_y: float, pitch_x: float, pitch_y: float) -> Tuple[float, float]:
    """
    Convert (row, col) to stage XY using A1 origin and pitch.
    row, col are 0-based (A=0, col1=0).
    """
    x = a1_x + col * pitch_x
    y = a1_y + row * pitch_y
    return x, y


def group_plan_by_fluid(plan: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    From the GUI plan (list of well entries), build a dict fluid -> list of dicts with row/col/volume/well_id.
    """
    by_fluid: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in plan:
        vols = entry.get("volumes_uL", {})
        r = int(entry["row"])
        c = int(entry["col"])
        wid = entry.get("well_id", f"{r},{c}")
        for lab, v in vols.items():
            if v and v > 0:
                by_fluid[lab].append({
                    "row": r, "col": c, "well_id": wid, "volume_uL": float(v)
                })
    # Sort each list row-major as a simple travel heuristic
    for lab in by_fluid.keys():
        by_fluid[lab].sort(key=lambda e: (e["row"], e["col"]))
    return dict(by_fluid)


def capture_plan_from_gui() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Launch the GUI from doserGUI, intercept send_to_backend to capture the plan and plate metadata, then close GUI.
    Returns (plan, plate_meta) where plate_meta has plate name/rows/cols/pitch.
    """
    app = gui.DosingApp()
    captured = {"plan": None, "plate": None}

    def _patched_send_to_backend(self, plan: List[Dict[str, Any]]):
        # Capture the plan and plate metadata, then close the GUI
        captured["plan"] = plan
        captured["plate"] = {
            "name": self.plate_spec.name,
            "rows": self.plate_spec.rows,
            "cols": self.plate_spec.cols,
            "pitch_mm": getattr(self.plate_spec, "pitch_mm", None),
        }
        try:
            self.destroy()
        except Exception:
            pass

    # Bind the patch so it's a proper bound method
    app.send_to_backend = types.MethodType(_patched_send_to_backend, app)

    # Run the GUI until user hits "Begin Dosing" (or closes the window)
    app.mainloop()

    if not captured["plan"]:
        raise RuntimeError("No dosing plan captured. The GUI may have been closed without starting dosing.")

    return captured["plan"], captured["plate"]


def validate_reservoirs(by_fluid: Dict[str, List[Dict[str, Any]]]):
    missing = [lab for lab in by_fluid.keys() if lab not in FLUID_RESERVOIRS]
    if missing:
        raise ValueError(f"Missing reservoir coordinates for fluids: {missing}. "
                         f"Update FLUID_RESERVOIRS in orchestrator.py.")


def resolve_plate_coords(plate_meta: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """
    Determine A1 origin and pitch to use. Prefer PLATE_OFFSETS by plate name; fallback to pitch from GUI.
    """
    name = plate_meta.get("name", "")
    rows = plate_meta.get("rows")
    cols = plate_meta.get("cols")
    pitch_gui = plate_meta.get("pitch_mm")

    if name in PLATE_OFFSETS:
        cfg = PLATE_OFFSETS[name]
        return cfg["a1_x"], cfg["a1_y"], cfg["pitch_x"], cfg["pitch_y"]

    if pitch_gui is None:
        raise ValueError(f"Unknown plate preset '{name}', and no pitch in GUI meta. "
                         f"Add plate coordinates to PLATE_OFFSETS.")

    # Fallback: use GUI pitch for both axes and default A1 origin
    print(f"[warn] Using fallback plate coords for '{name}'. Please add to PLATE_OFFSETS.")
    return 63.5, 81.5, pitch_gui, pitch_gui


# -------------------- Orchestration --------------------

def run_dosing(ser, plan: List[Dict[str, Any]], plate_meta: Dict[str, Any], overdraw_uL: float = 0.0):
    """
    Execute per-fluid dosing sequence using hardwareInterfaces for motion.

    New sequence (per fluid, per well):
      reservoir -> withdraw (v + overdraw) -> well -> dispense v -> repeat
    """
    # Resolve plate coordinates
    a1_x, a1_y, pitch_x, pitch_y = resolve_plate_coords(plate_meta)

    # Group targets by fluid
    by_fluid = group_plan_by_fluid(plan)
    if not by_fluid:
        print("No dosing required (all zero).")
        return

    validate_reservoirs(by_fluid)

    # Initialize motion system
    hw.command(ser, "G28")         # home
    hw.command(ser, "G21")         # mm units
    hw.command(ser, "G90")         # absolute
    hw.command(ser, "M203 Z1200")  # Z speed limit
    time.sleep(0.1)

    # Lift to safe travel Z
    hw.command(ser, f"G1 Z{TRAVEL_Z:.2f} F{F_Z}")
    hw.command(ser, "M400")

    for lab, wells in by_fluid.items():
        res_xy = FLUID_RESERVOIRS[lab]
        print(f"\n=== Fluid {lab}: {len(wells)} wells ===")

        for w in wells:
            wx, wy = well_to_xy(w["row"], w["col"], a1_x, a1_y, pitch_x, pitch_y)
            v = float(w["volume_uL"])
            wid = w["well_id"]
            pickup = v + max(0.0, overdraw_uL)

            # 1) Go to fluid reservoir and withdraw (aspirate) pickup volume
            hw.moveTo(ser, res_xy[0], res_xy[1], TRAVEL_Z, zlift=TRAVEL_Z, speed=F_XY)
            hw.dosePositioning(ser, z=ASPIRATE_Z, speed=F_Z)
            if hasattr(hw, "withdraw"):
                hw.withdraw(pickup)
            elif hasattr(hw, "aspirate"):
                hw.aspirate(pickup)
            else:
                print(f"[info] No pump API in hardwareInterfaces. Skipping aspiration of {pickup:.2f} uL.")
            hw.dosePositioning(ser, z=TRAVEL_Z, speed=F_Z)

            # 2) Go to target well and dispense (infuse) actual volume
            print(f"[{lab}] {wid} -> ({wx:.2f}, {wy:.2f}): {v:.2f} uL")
            hw.moveTo(ser, wx, wy, TRAVEL_Z, zlift=TRAVEL_Z, speed=F_XY)
            hw.dosePositioning(ser, z=DOSE_Z, speed=F_Z)

            # If you actually need to aspirate at the well and dispense at the reservoir, swap the two blocks:
            if hasattr(hw, "infuse"):
                hw.infuse(v)
            elif hasattr(hw, "dispense"):
                hw.dispense(v)
            else:
                print(f"[info] No pump API in hardwareInterfaces. Skipping dispense of {v:.2f} uL.")

            hw.dosePositioning(ser, z=TRAVEL_Z, speed=F_Z)

    print("\nDosing complete.")


# -------------------- Main --------------------

def main():
    ap = argparse.ArgumentParser(description="Mediator: import GUI from doserGUI and movement from hardwareInterfaces.")
    ap.add_argument("--port", default="COM5", help="Serial port for motion controller (e.g., COM5, /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--plan", default=None, help="Optional: path to a JSON plan (skip GUI).")
    args = ap.parse_args()

    # Load plan either from GUI (default) or JSON file
    if args.plan:
        with open(args.plan, "r") as f:
            plan = json.load(f)
        # If you provide a plan file, set plate meta or rely on defaults
        # You can encode plate name/rows/cols/pitch in a sidecar file if needed.
        plate_meta = {"name": "96-well (8x12)", "rows": 8, "cols": 12, "pitch_mm": 9.0}
    else:
        plan, plate_meta = capture_plan_from_gui()

    # Open serial and run
    ser = serial.Serial(args.port, args.baud, timeout=2)
    time.sleep(2)
    try:
        run_dosing(ser, plan, plate_meta)
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()