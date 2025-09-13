# dosing_runner.py
# Orchestrates dosing: per-liquid, per-well, using placeholder hardware calls.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import json
import time

# Import the hardware layer
from hardwareInterfaces import Pump, EnderMovement


@dataclass
class Reservoir:
    label: str
    x_mm: float
    y_mm: float
    z_aspirate_mm: float
    z_clear_mm: float


@dataclass
class PlateLayout:
    """
    Defines how to translate (row, col) to printer XY coordinates.
    A1 is row=0, col=0.
    """
    name: str
    rows: int
    cols: int
    a1_x_mm: float
    a1_y_mm: float
    col_pitch_mm: float
    row_pitch_mm: float
    y_positive_down: bool = True  # GUI rows increase "downwards" in pixels; set True if Y increases in that direction on the machine


@dataclass
class DosingConfig:
    # Z heights
    plate_clear_z_mm: float = 10.0
    plate_dispense_z_mm: float = 2.0
    # Motion
    travel_feedrate: int = 3000
    z_feedrate: int = 800
    swirl_feedrate: int = 1200
    # Swirl to remove drips after withdrawing
    reservoir_swirly_radius_mm: float = 1.5
    reservoir_swirly_loops: int = 1
    # Pump
    pump_withdraw_rate_ul_s: float = 50.0
    pump_infuse_rate_ul_s: float = 50.0
    # Optional small delays (seconds)
    delay_after_withdraw_s: float = 0.2
    delay_after_dispense_s: float = 0.2


class DosingExecutor:
    """
    Takes a dosing plan (from GUI), hardware interfaces, and runs a per-liquid dosing routine.
    """
    def __init__(
        self,
        pump: Pump,
        mover: EnderMovement,
        source_positions: Dict[str, Reservoir],
        plate_layout: PlateLayout,
        config: Optional[DosingConfig] = None
    ):
        self.pump = pump
        self.mover = mover
        self.source_positions = source_positions
        self.plate_layout = plate_layout
        self.cfg = config or DosingConfig()

    def run(self, plan: List[dict] | str, sources_order: Optional[List[str]] = None):
        """
        plan: list of dicts as produced by the GUI, or JSON string of that list.
        sources_order: ordered list of source labels (e.g., ["A","B","C","D","E","F"]).
                       If None, will infer labels sorted alphabetically.
        """
        entries = self._parse_plan(plan)
        if not entries:
            print("[RUN] Empty plan; nothing to do.")
            return

        # Ensure hardware is ready
        self._ensure_connected()

        # Determine dosing order
        if sources_order is None:
            sources_order = self._infer_sources_order(entries)
        print(f"[RUN] Dosing order: {sources_order}")

        # Group wells by source (only those with > 0 volume for that source)
        wells_by_source = self._group_wells_by_source(entries)

        # Iterate over liquids in the provided order
        for src_label in sources_order:
            # Resolve reservoir position
            reservoir = self.source_positions.get(src_label)
            if reservoir is None:
                print(f"[WARN] No reservoir position defined for source '{src_label}'. Skipping.")
                continue

            wells = wells_by_source.get(src_label, [])
            if not wells:
                print(f"[RUN] No wells require source '{src_label}'. Skipping.")
                continue

            print(f"[RUN] Processing liquid '{src_label}' for {len(wells)} well(s).")

            # For each well that needs this source
            for entry in wells:
                vol_ul = float(entry["volumes_uL"].get(src_label, 0.0))
                if vol_ul <= 0:
                    continue

                # 1) Go to liquid XYZ location -> above reservoir
                self._move_to_reservoir_clear(reservoir)

                # 2) Suck up specified volume for that cell
                self._aspirate_from_reservoir(reservoir, vol_ul)

                # 3) Do a little swirly movement to remove any drips
                self._swirl_at_reservoir(reservoir)

                # 4) Move to cell location
                x_mm, y_mm = self._well_xy_mm(entry["row"], entry["col"])
                self._move_to_well_clear(x_mm, y_mm)

                # 5) Dose liquid
                self._dispense_into_well(x_mm, y_mm, vol_ul)

                # 6) Repeat for all cells (loop continues)

        print("[RUN] Dosing complete.")

    # ---------------- High-level steps ----------------

    def _move_to_reservoir_clear(self, res: Reservoir):
        self.mover.go_to(z=res.z_clear_mm, feedrate=self.cfg.z_feedrate)
        self.mover.go_to(x=res.x_mm, y=res.y_mm, feedrate=self.cfg.travel_feedrate)

    def _aspirate_from_reservoir(self, res: Reservoir, volume_ul: float):
        # Move down to aspirate height
        self.mover.go_to(z=res.z_aspirate_mm, feedrate=self.cfg.z_feedrate, rapid=False)
        # Withdraw
        self.pump.withdraw(volume_ul, rate_ul_s=self.cfg.pump_withdraw_rate_ul_s)
        if self.cfg.delay_after_withdraw_s > 0:
            self.mover.dwell(self.cfg.delay_after_withdraw_s)
        # Lift to clear
        self.mover.go_to(z=res.z_clear_mm, feedrate=self.cfg.z_feedrate)

    def _swirl_at_reservoir(self, res: Reservoir):
        # Perform small swirl above the reservoir to knock drips
        self.mover.swirly(
            center_x=res.x_mm,
            center_y=res.y_mm,
            z=res.z_clear_mm,
            radius=self.cfg.reservoir_swirly_radius_mm,
            loops=self.cfg.reservoir_swirly_loops,
            feedrate=self.cfg.swirl_feedrate
        )

    def _move_to_well_clear(self, x_mm: float, y_mm: float):
        # Move above the well at safe Z then to XY
        self.mover.go_to(z=self.cfg.plate_clear_z_mm, feedrate=self.cfg.z_feedrate)
        self.mover.go_to(x=x_mm, y=y_mm, feedrate=self.cfg.travel_feedrate)

    def _dispense_into_well(self, x_mm: float, y_mm: float, volume_ul: float):
        # Move down to dispense height
        self.mover.go_to(z=self.cfg.plate_dispense_z_mm, feedrate=self.cfg.z_feedrate, rapid=False)
        # Infuse
        self.pump.infuse(volume_ul, rate_ul_s=self.cfg.pump_infuse_rate_ul_s)
        if self.cfg.delay_after_dispense_s > 0:
            self.mover.dwell(self.cfg.delay_after_dispense_s)
        # Lift
        self.mover.go_to(z=self.cfg.plate_clear_z_mm, feedrate=self.cfg.z_feedrate)

    # ---------------- Helpers ----------------

    def _ensure_connected(self):
        if not self.pump.connected:
            self.pump.connect()
        if not self.mover.connected:
            self.mover.connect()

    def _parse_plan(self, plan: List[dict] | str) -> List[dict]:
        if isinstance(plan, str):
            try:
                data = json.loads(plan)
            except json.JSONDecodeError as e:
                print(f"[ERR] Invalid plan JSON: {e}")
                return []
        else:
            data = plan

        # Basic validation
        norm = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if "row" not in entry or "col" not in entry or "volumes_uL" not in entry:
                continue
            norm.append(entry)
        return norm

    def _infer_sources_order(self, entries: List[dict]) -> List[str]:
        labels = set()
        for e in entries:
            labels.update(e.get("volumes_uL", {}).keys())
        return sorted(labels)

    def _group_wells_by_source(self, entries: List[dict]) -> Dict[str, List[dict]]:
        by_src: Dict[str, List[dict]] = {}
        for e in entries:
            volmap = e.get("volumes_uL", {})
            for label, v in volmap.items():
                try:
                    vnum = float(v)
                except Exception:
                    vnum = 0.0
                if vnum > 0:
                    by_src.setdefault(label, []).append(e)
        # Optional stable ordering by (row, col)
        for label, lst in by_src.items():
            lst.sort(key=lambda x: (x["row"], x["col"]))
        return by_src

    def _well_xy_mm(self, row: int, col: int) -> tuple[float, float]:
        """
        Convert well (row, col) to machine XY using the plate layout.
        """
        dx = col * self.plate_layout.col_pitch_mm
        dy = row * self.plate_layout.row_pitch_mm
        if not self.plate_layout.y_positive_down:
            dy = -dy
        x = self.plate_layout.a1_x_mm + dx
        y = self.plate_layout.a1_y_mm + dy
        return (x, y)


# ---------------- Convenience entry points ----------------

def run_dosing_with_json(
    plan_json: str,
    sources_order: List[str],
    pump: Pump,
    mover: EnderMovement,
    source_positions: Dict[str, Reservoir],
    plate_layout: PlateLayout,
    config: Optional[DosingConfig] = None
):
    """
    Helper to call from your GUI: pass the JSON string printed/returned by the GUI, and the ordered source labels.
    """
    executor = DosingExecutor(
        pump=pump,
        mover=mover,
        source_positions=source_positions,
        plate_layout=plate_layout,
        config=config
    )
    executor.run(plan_json, sources_order=sources_order)


# ---------------- Example usage (manual test) ----------------
if __name__ == "__main__":
    # Example fake plan with 3 wells; replace this with GUI JSON.
    example_plan = [
        {"well_id": "A1", "row": 0, "col": 0, "center_px": [100.0, 100.0], "volumes_uL": {"A": 10, "B": 0, "C": 5}},
        {"well_id": "A2", "row": 0, "col": 1, "center_px": [120.0, 100.0], "volumes_uL": {"A": 5, "B": 2}},
        {"well_id": "B1", "row": 1, "col": 0, "center_px": [100.0, 120.0], "volumes_uL": {"B": 7}},
    ]

    # Example hardware (dry-run prints)
    pump = Pump(dry_run=True)
    mover = EnderMovement(dry_run=True)

    # Define your reservoir positions for each source label (in mm)
    source_positions = {
        "A": Reservoir("A", x_mm=50.0, y_mm=20.0, z_aspirate_mm=1.5, z_clear_mm=8.0),
        "B": Reservoir("B", x_mm=70.0, y_mm=20.0, z_aspirate_mm=1.5, z_clear_mm=8.0),
        "C": Reservoir("C", x_mm=90.0, y_mm=20.0, z_aspirate_mm=1.5, z_clear_mm=8.0),
        "D": Reservoir("D", x_mm=110.0, y_mm=20.0, z_aspirate_mm=1.5, z_clear_mm=8.0),
        "E": Reservoir("E", x_mm=130.0, y_mm=20.0, z_aspirate_mm=1.5, z_clear_mm=8.0),
        "F": Reservoir("F", x_mm=150.0, y_mm=20.0, z_aspirate_mm=1.5, z_clear_mm=8.0),
    }

    # Plate layout (example for 96-well)
    plate_layout = PlateLayout(
        name="96-well (8x12)",
        rows=8,
        cols=12,
        a1_x_mm=200.0,   # machine coordinates for well A1
        a1_y_mm=100.0,
        col_pitch_mm=9.0,
        row_pitch_mm=9.0,
        y_positive_down=True  # set False if your Y increases upward relative to rows
    )

    cfg = DosingConfig()
    executor = DosingExecutor(pump, mover, source_positions, plate_layout, cfg)
    executor.run(example_plan, sources_order=["A", "B", "C", "D", "E", "F"])