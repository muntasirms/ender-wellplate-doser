import tkinter as tk
from tkinter import ttk, messagebox
from dataclasses import dataclass
import json
import math

# --------------- Models and Utilities ---------------

def index_to_letters(idx_zero_based: int) -> str:
    # 0->A, 25->Z, 26->AA, ...
    s = ""
    n = idx_zero_based + 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

@dataclass
class Source:
    label: str
    color: str  # hex

@dataclass
class PlateSpec:
    name: str
    rows: int
    cols: int
    pitch_mm: float = 9.0  # typical 96-well pitch in mm (not essential here)

class DosingModel:
    def __init__(self, plate_spec: PlateSpec, sources: list[Source]):
        self.plate = plate_spec
        self.sources = sources[:]  # list of Source
        # Map well_id -> data
        self.wells = {}
        self._init_wells()

    def set_plate(self, plate_spec: PlateSpec):
        self.plate = plate_spec
        self._init_wells()

    def set_sources(self, sources: list[Source]):
        self.sources = sources[:]
        # Rebuild volume dictionaries for all wells
        for wid, wdata in self.wells.items():
            new_vols = {s.label: 0.0 for s in self.sources}
            # If labels overlap, carry over previous values
            for label, v in wdata['volumes'].items():
                if label in new_vols:
                    new_vols[label] = v
            wdata['volumes'] = new_vols

    def _init_wells(self):
        self.wells = {}
        for r in range(self.plate.rows):
            for c in range(self.plate.cols):
                wid = self.well_id(r, c)
                self.wells[wid] = {
                    'row': r,
                    'col': c,
                    'volumes': {s.label: 0.0 for s in self.sources},
                }

    def well_id(self, row: int, col: int) -> str:
        return f"{index_to_letters(row)}{col+1}"

    def total_volume(self, well_id: str) -> float:
        return sum(self.wells[well_id]['volumes'].values())

# --------------- GUI Application ---------------

class DosingApp(tk.Tk):
    def __init__(self):
        super().__init__()

        # -------- Configuration --------
        self.title("Liquid Dosing GUI")
        self.template_width = 1200
        self.template_height = 800
        self.geometry(f"{self.template_width}x{self.template_height}")
        self.resizable(False, False)

        # Layout regions
        self.margin = 16
        self.top_bar_h = 130
        self.bottom_bar_h = 90

        # Sources (extensible)
        default_source_labels = ["A", "B", "C", "D", "E", "F"]
        default_source_colors = ["#FF6B6B", "#4D96FF", "#6EE7B7", "#FFB020", "#B794F4", "#FF8FAB"]
        self.sources = [Source(lab, default_source_colors[i % len(default_source_colors)])
                        for i, lab in enumerate(default_source_labels)]

        # Plate presets (extensible)
        self.plate_presets = [
            PlateSpec("96-well (8x12)", rows=8, cols=12, pitch_mm=9.0),
            PlateSpec("384-well (16x24)", rows=16, cols=24, pitch_mm=4.5),
            PlateSpec("24-well (4x6)", rows=4, cols=6, pitch_mm=19.3),
        ]
        self.plate_spec = self.plate_presets[0]

        # Model
        self.model = DosingModel(self.plate_spec, self.sources)

        # Max per-well volume (user configurable)
        self.max_well_volume_var = tk.DoubleVar(value=200.0)  # uL by default

        # Canvas root
        self.canvas = tk.Canvas(self, width=self.template_width, height=self.template_height, bg="#F7F7F7", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # For referencing drawn items
        self.source_circle_ids = []
        self.well_item_map = {}  # well_id -> canvas item id
        self.well_centers_px = {}  # well_id -> (x, y)

        # UI elements (widgets created inside canvas via create_window)
        self.plate_combo = None
        self.begin_button = None

        # Draw UI
        self.draw_template()
        self.draw_top_bar()
        self.draw_bottom_bar()
        self.draw_plate()

    # --------------- Drawing ---------------

    def draw_template(self):
        # Outer border rectangle
        self.canvas.delete("template_border")
        self.canvas.create_rectangle(
            self.margin, self.margin, self.template_width - self.margin, self.template_height - self.margin,
            outline="#B0B0B0", width=2, tags="template_border"
        )

    def draw_top_bar(self):
        # Clear previous
        self.canvas.delete("top_bar")

        left = self.margin
        top = self.margin
        right = self.template_width - self.margin
        bottom = top + self.top_bar_h

        # Draw top bar background
        self.canvas.create_rectangle(left, top, right, bottom, fill="#FFFFFF", outline="#E2E2E2", width=1, tags="top_bar")

        # Plate selector (top-left)
        self.canvas.create_text(left + 10, top + 12, text="Plate:", anchor="nw", font=("Segoe UI", 10), fill="#333", tags="top_bar")
        plate_names = [p.name for p in self.plate_presets]
        self.plate_combo_var = tk.StringVar(value=self.plate_spec.name)
        self.plate_combo = ttk.Combobox(self, textvariable=self.plate_combo_var, values=plate_names, state="readonly", width=20)
        self.plate_combo.bind("<<ComboboxSelected>>", self.on_plate_changed)
        self.canvas.create_window(left + 60, top + 10, anchor="nw", window=self.plate_combo, tags="top_bar")

        # Draw source circles across the top bar
        n = len(self.sources)
        if n == 0:
            return

        # available width for sources region
        src_region_left = left + 250
        src_region_right = right - 20
        region_w = max(100, src_region_right - src_region_left)
        region_h = self.top_bar_h - 20

        segment_w = region_w / n
        radius = min((segment_w * 0.6) / 2, (region_h * 0.75) / 2)
        cy = top + self.top_bar_h / 2 + 5  # slightly lowered center
        self.source_circle_ids.clear()

        for i, src in enumerate(self.sources):
            cx = src_region_left + (i + 0.5) * segment_w
            # Circle
            cid = self.canvas.create_oval(
                cx - radius, cy - radius, cx + radius, cy + radius,
                fill=src.color, outline="#333333", width=2, tags=("top_bar", "source_circle")
            )
            self.source_circle_ids.append(cid)
            # Label on circle
            self.canvas.create_text(cx, cy, text=src.label, font=("Segoe UI", 16, "bold"),
                                    fill="#FFFFFF", tags="top_bar")

        # Title (optional)
        self.canvas.create_text(left + 10, top + 40, anchor="nw",
                                text="Liquid Sources", font=("Segoe UI", 12, "bold"),
                                fill="#333", tags="top_bar")

    def draw_bottom_bar(self):
        self.canvas.delete("bottom_bar")

        left = self.margin
        bottom = self.template_height - self.margin
        right = self.template_width - self.margin
        top = bottom - self.bottom_bar_h

        # Draw bottom bar background
        self.canvas.create_rectangle(left, top, right, bottom, fill="#FFFFFF", outline="#E2E2E2", width=1, tags="bottom_bar")

        # Begin dosing button (bottom-left)
        if self.begin_button:
            self.begin_button.destroy()
        self.begin_button = ttk.Button(self, text="Begin Dosing", command=self.begin_dosing)
        self.canvas.create_window(left + 12, top + 12, anchor="nw", window=self.begin_button, tags="bottom_bar")

        # Max per-well volume control (bottom center-ish)
        self.canvas.create_text((left + right) // 2 - 120, top + 18, anchor="e",
                                text="Max per-well volume (µL):", font=("Segoe UI", 10),
                                fill="#333", tags="bottom_bar")
        maxvol_entry = ttk.Spinbox(self, from_=0, to=100000, increment=1, width=8, textvariable=self.max_well_volume_var)
        self.canvas.create_window((left + right) // 2 - 110, top + 12, anchor="nw", window=maxvol_entry, tags="bottom_bar")

        # Bottom-right: two arrows (Location calibration and Liquid dosing calibration)
        # We'll draw right-pointing arrows with labels, stacked vertically.
        pad = 10
        arrow_w = 120
        arrow_h = 36
        gap = 10

        x_arrow = right - pad - arrow_w
        y_arrow1 = top + pad
        y_arrow2 = y_arrow1 + arrow_h + gap

        self.draw_arrow_button(x_arrow, y_arrow1, arrow_w, arrow_h, "Location Cal", "loc_cal", self.on_location_calibration)
        self.draw_arrow_button(x_arrow, y_arrow2, arrow_w, arrow_h, "Liquid Cal", "liq_cal", self.on_liquid_calibration)

    def draw_arrow_button(self, x, y, w, h, text, tag, callback):
        # Draw a right-pointing arrow shape with label
        # Arrow polygon
        body_w = int(w * 0.7)
        tip_w = w - body_w
        pts = [
            (x, y), (x + body_w, y), (x + body_w + tip_w, y + h // 2),
            (x + body_w, y + h), (x, y + h)
        ]
        poly = self.canvas.create_polygon(
            sum(pts, ()), fill="#EAEAEA", outline="#B5B5B5", width=1, tags=("bottom_bar", tag)
        )
        # Label
        lbl = self.canvas.create_text(x + int(body_w * 0.5), y + h // 2, text=text, font=("Segoe UI", 10, "bold"),
                                      fill="#333", tags=("bottom_bar", tag))
        # Hover effect & click
        def on_enter(_e, pid=poly):
            self.canvas.itemconfig(pid, fill="#DCDCDC")
            self.config(cursor="hand2")
        def on_leave(_e, pid=poly):
            self.canvas.itemconfig(pid, fill="#EAEAEA")
            self.config(cursor="")
        def on_click(_e):
            callback()

        for t in (tag,):
            self.canvas.tag_bind(t, "<Enter>", on_enter)
            self.canvas.tag_bind(t, "<Leave>", on_leave)
            self.canvas.tag_bind(t, "<Button-1>", on_click)

    def draw_plate(self):
        # Clear previous plate drawing
        self.canvas.delete("plate")
        self.well_item_map.clear()
        self.well_centers_px.clear()

        # Compute center region available for plate
        left = self.margin
        top = self.margin + self.top_bar_h
        right = self.template_width - self.margin
        bottom = self.template_height - self.margin - self.bottom_bar_h

        # Draw center area background
        self.canvas.create_rectangle(left, top, right, bottom, fill="#FAFAFA", outline="#E9E9E9", width=1, tags="plate")

        # Determine plate grid area with padding and centering
        pad = 40
        inner_left = left + pad
        inner_right = right - pad
        inner_top = top + pad
        inner_bottom = bottom - pad

        region_w = inner_right - inner_left
        region_h = inner_bottom - inner_top

        rows = self.plate_spec.rows
        cols = self.plate_spec.cols

        # Compute pitch in pixels to fit whole grid
        # We place centers on an (rows x cols) grid with (cols-1) gaps horizontally, etc.
        if cols > 1:
            pitch_x = region_w / (cols - 1)
        else:
            pitch_x = min(region_w, region_h)  # arbitrary when 1 col
        if rows > 1:
            pitch_y = region_h / (rows - 1)
        else:
            pitch_y = min(region_w, region_h)  # arbitrary when 1 row

        pitch = min(pitch_x, pitch_y)

        # Well radius as fraction of pitch (typical diameter ~ 0.36 of pitch)
        well_radius = max(6, pitch * 0.35 / 2 * 2)  # ensure min pixel size

        # Adjust starting point to center the plate
        total_w = (cols - 1) * pitch
        total_h = (rows - 1) * pitch
        start_x = inner_left + (region_w - total_w) / 2
        start_y = inner_top + (region_h - total_h) / 2

        # Labels for rows/cols
        label_font = ("Segoe UI", 9)
        # Column numbers top
        for c in range(cols):
            cx = start_x + c * pitch
            self.canvas.create_text(cx, start_y - 18, text=str(c + 1), font=label_font,
                                    fill="#555", tags="plate")
        # Row letters left
        for r in range(rows):
            cy = start_y + r * pitch
            self.canvas.create_text(start_x - 18, cy, text=index_to_letters(r), font=label_font,
                                    fill="#555", tags="plate")

        # Draw wells
        for r in range(rows):
            for c in range(cols):
                cx = start_x + c * pitch
                cy = start_y + r * pitch
                wid = self.model.well_id(r, c)

                item_id = self.canvas.create_oval(
                    cx - well_radius, cy - well_radius, cx + well_radius, cy + well_radius,
                    fill="#FFFFFF", outline="#444", width=1.5, tags=("plate", f"well_{wid}")
                )

                # Tooltip-ish label inside or below? Keep clean; only on hover we can change cursor.
                self.well_item_map[wid] = item_id
                self.well_centers_px[wid] = (cx, cy)

                # Bind click
                def make_handler(well_id):
                    return lambda e: self.edit_well_volumes(well_id)
                self.canvas.tag_bind(f"well_{wid}", "<Button-1>", make_handler(wid))
                self.canvas.tag_bind(f"well_{wid}", "<Enter>", lambda e, i=item_id: self.on_well_hover(i, True))
                self.canvas.tag_bind(f"well_{wid}", "<Leave>", lambda e, i=item_id: self.on_well_hover(i, False))

                # Initial visual state based on volumes (all zero)
                self.update_well_visual(wid)

        # Plate title
        title = f"{self.plate_spec.name}  ({self.plate_spec.rows} x {self.plate_spec.cols})"
        self.canvas.create_text((left + right) // 2, top + 18, text=title, font=("Segoe UI", 12, "bold"),
                                fill="#333", tags="plate")

    # --------------- Interactions ---------------

    def on_well_hover(self, item_id, enter: bool):
        if enter:
            self.canvas.itemconfig(item_id, width=2.5)
            self.config(cursor="hand2")
        else:
            self.canvas.itemconfig(item_id, width=1.5)
            self.config(cursor="")

    def edit_well_volumes(self, well_id: str):
        # Open a modal pop-up to set volumes from each source for this well
        top = tk.Toplevel(self)
        top.title(f"Set Volumes for {well_id}")
        top.transient(self)
        top.grab_set()
        top.resizable(False, False)

        frm = ttk.Frame(top, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=f"Well {well_id}", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, pady=(0, 8), sticky="w")

        entries = {}
        row_idx = 1
        for s in self.sources:
            ttk.Label(frm, text=f"{s.label} (µL):").grid(row=row_idx, column=0, sticky="e", padx=(0, 6), pady=3)
            var = tk.StringVar(value=str(self.model.wells[well_id]['volumes'].get(s.label, 0.0)))
            ent = ttk.Entry(frm, textvariable=var, width=10)
            ent.grid(row=row_idx, column=1, sticky="w", pady=3)
            # Color swatch
            sw = tk.Canvas(frm, width=18, height=18, bg=s.color, highlightthickness=1, highlightbackground="#999")
            sw.grid(row=row_idx, column=2, padx=(8, 0))
            entries[s.label] = var
            row_idx += 1

        sum_lbl = ttk.Label(frm, text="Total: 0 µL", foreground="#333")
        sum_lbl.grid(row=row_idx, column=0, columnspan=2, sticky="w", pady=(8, 4))
        row_idx += 1

        # compute initial summary
        def compute_total():
            total = 0.0
            for lab, var in entries.items():
                try:
                    v = float(var.get().strip() or "0")
                    if v < 0:
                        raise ValueError
                    total += v
                except ValueError:
                    total = math.inf
                    break
            max_vol = self.max_well_volume_var.get()
            if total == math.inf:
                sum_lbl.config(text=f"Total: invalid input", foreground="#B00020")
            else:
                color = "#B00020" if total > max_vol else "#333"
                sum_lbl.config(text=f"Total: {total:.2f} µL (max {max_vol:.2f})", foreground=color)

        for var in entries.values():
            var.trace_add("write", lambda *args: compute_total())
        compute_total()

        btnfrm = ttk.Frame(frm)
        btnfrm.grid(row=row_idx, column=0, columnspan=3, pady=(10, 0), sticky="e")

        def on_save():
            new_vols = {}
            # Validate per-field
            for lab, var in entries.items():
                txt = var.get().strip()
                if txt == "":
                    txt = "0"
                try:
                    v = float(txt)
                    if v < 0:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("Invalid value", f"Volume for source {lab} must be a non-negative number.")
                    return
                new_vols[lab] = v
            # Check total
            total = sum(new_vols.values())
            max_vol = float(self.max_well_volume_var.get())
            if total > max_vol:
                messagebox.showerror("Volume exceeds maximum",
                                     f"The total volume for well {well_id} ({total:.2f} µL) exceeds the maximum ({max_vol:.2f} µL).")
                return
            # Save into model
            self.model.wells[well_id]['volumes'] = new_vols
            self.update_well_visual(well_id)
            top.destroy()

        def on_cancel():
            top.destroy()

        ttk.Button(btnfrm, text="Cancel", command=on_cancel).pack(side="right", padx=(4, 0))
        ttk.Button(btnfrm, text="Save", command=on_save).pack(side="right", padx=(4, 0))

        # Center the popup
        self.center_window(top, width=320, height=60 + 32 * len(self.sources))

    def update_well_visual(self, well_id: str):
        # Visual cue: if total is zero -> white; if > 0 -> light tint; if exceeds max -> red outline
        item = self.well_item_map.get(well_id)
        if not item:
            return
        total = self.model.total_volume(well_id)
        max_vol = self.max_well_volume_var.get()
        if total == 0:
            fill = "#FFFFFF"
            outline = "#444444"
        else:
            fill = "#FFF7D6"
            outline = "#444444"
        if total > max_vol:
            outline = "#C62828"
        self.canvas.itemconfig(item, fill=fill, outline=outline)

    def on_plate_changed(self, _event=None):
        selected = self.plate_combo_var.get()
        match = next((p for p in self.plate_presets if p.name == selected), None)
        if match is not None:
            self.plate_spec = match
            # Reset model to new plate
            self.model.set_plate(self.plate_spec)
            # Redraw
            self.draw_plate()

    def begin_dosing(self):
        # Validate all wells against max volume
        max_vol = float(self.max_well_volume_var.get())
        errors = []
        for wid, wdata in self.model.wells.items():
            total = sum(wdata['volumes'].values())
            if total > max_vol:
                errors.append((wid, total))
        if errors:
            # Highlight offending wells, show error
            for wid, _ in errors:
                self.canvas.itemconfig(self.well_item_map[wid], outline="#C62828")
            msg = "The following wells exceed the maximum volume:\n\n"
            msg += "\n".join([f"{wid}: {t:.2f} µL (max {max_vol:.2f})" for wid, t in errors])
            messagebox.showerror("Dosing Error", msg)
            return

        # Build dosing plan (send to backend)
        plan = self.build_dosing_plan()
        self.send_to_backend(plan)

    def build_dosing_plan(self):
        # Construct a list of entries for wells with non-zero total volume
        plan = []
        for wid, wdata in self.model.wells.items():
            total = sum(wdata['volumes'].values())
            if total <= 0:
                continue
            cx, cy = self.well_centers_px.get(wid, (None, None))
            entry = {
                "well_id": wid,
                "row": wdata['row'],
                "col": wdata['col'],
                "center_px": (cx, cy),
                "volumes_uL": dict(wdata['volumes']),
            }
            plan.append(entry)
        return plan

    def send_to_backend(self, plan: list[dict]):
        # Placeholder backend call. Replace this with your integration.
        # For example, publish via socket/serial/API to your robot/PLC.
        print("---- Dosing Plan (to backend) ----")
        print(json.dumps(plan, indent=2))
        print("---- End Plan ----")
        messagebox.showinfo("Dosing", f"Dosing plan prepared for {len(plan)} wells.\n(Printed to console as JSON.)")

    def on_location_calibration(self):
        # Placeholder for location calibration routine
        messagebox.showinfo("Calibration", "Starting Location Calibration...\n(Placeholder function)")
        print("[Calibration] Location calibration started (placeholder).")

    def on_liquid_calibration(self):
        # Placeholder for liquid dosing calibration routine
        messagebox.showinfo("Calibration", "Starting Liquid Dosing Calibration...\n(Placeholder function)")
        print("[Calibration] Liquid dosing calibration started (placeholder).")

    # --------------- Helpers ---------------
    def center_window(self, win: tk.Toplevel, width: int, height: int):
        self.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - width) // 2
        y = self.winfo_rooty() + (self.winfo_height() - height) // 2
        win.geometry(f"{width}x{height}+{x}+{y}")


if __name__ == "__main__":
    app = DosingApp()
    app.mainloop()
