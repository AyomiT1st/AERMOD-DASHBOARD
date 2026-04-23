import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cartopy.io.img_tiles as cimgt
import streamlit as st
import re
import io
from pathlib import Path


# =========================
# PARSERS
# =========================

def parse_grid_table(block_text: str):
    """
    Parse one page of an AERMOD grid concentration table.
    X coords are on the (METERS) | 349500.00 ... line.
    Y coords are row labels before the pipe.
    Returns x_vals (1d array), rows (dict {y: [values]})
    """
    lines = block_text.splitlines()

    x_vals = []
    for line in lines:
        if "|" in line and "METERS" in line:
            after_pipe = line.split("|", 1)[1]
            vals = re.findall(r"[\d]+\.[0-9]+", after_pipe)
            if vals:
                x_vals.extend([float(v) for v in vals])

    if not x_vals:
        return None, None

    x_vals = np.array(x_vals)

    row_pattern = re.compile(r"^\s*([\d.]+)\s*\|(.*)$")
    continuation_pattern = re.compile(r"^\s{10,}([\d.\s]+)$")

    rows = {}
    current_y = None

    for line in lines:
        row_match = row_pattern.match(line)
        if row_match:
            current_y = float(row_match.group(1))
            # Skip the (METERS) header line
            if current_y in x_vals:
                current_y = None
                continue
            vals = re.findall(r"[\d.]+(?:\.[0-9]+)?", row_match.group(2))
            rows[current_y] = [float(v) for v in vals]
        elif current_y is not None:
            cont_match = continuation_pattern.match(line)
            if cont_match:
                vals = re.findall(r"[\d.]+(?:\.[0-9]+)?", cont_match.group(1))
                if vals:
                    rows[current_y].extend([float(v) for v in vals])

    return x_vals, rows


def merge_pages(pages):
    """
    Merge multiple pages of the same averaging period horizontally.
    AERMOD splits wide grids across multiple pages when columns
    exceed the page width.
    Returns x_vals (1d), y_vals (1d), Z (2d, ny x nx)
    """
    all_x = []
    for x_vals, _ in pages:
        all_x.extend(x_vals.tolist())
    all_x = np.array(sorted(set(all_x)))

    all_y = set()
    for _, rows in pages:
        all_y.update(rows.keys())
    all_y = np.array(sorted(all_y))

    nx = len(all_x)
    ny = len(all_y)
    Z = np.full((ny, nx), np.nan)

    for x_vals, rows in pages:
        for iy, y in enumerate(all_y):
            if y not in rows:
                continue
            row_vals = rows[y]
            for col_i, x in enumerate(x_vals):
                ix = np.searchsorted(all_x, x)
                if col_i < len(row_vals):
                    Z[iy, ix] = row_vals[col_i]

    return all_x, all_y, Z


def parse_background(text: str) -> dict:
    """
    Extract background concentrations echoed in the .out file.
    Handles HROFDY, SEASHR, WSPEED, MONTHLY, ANNUAL types.
    When background is included, AERMOD output already contains
    it in the reported concentrations. This is for display only.
    """
    bg_pattern = re.compile(
        r"BACKGRND\s+(HROFDY|SEASHR|WSPEED|MONTHLY|ANNUAL)\s+([\d.\s]+)",
        re.IGNORECASE
    )
    matches = bg_pattern.findall(text)
    if not matches:
        return {"present": False}

    bg_type = matches[0][0].upper()
    all_values = []
    for _, val_str in matches:
        all_values.extend([float(v) for v in val_str.split()])

    return {
        "present": True,
        "type":    bg_type,
        "values":  all_values,
        "mean":    np.mean(all_values),
        "max":     np.max(all_values),
        "min":     np.min(all_values),
    }


def parse_aermod_out(filepath):
    """
    Parse an AERMOD .out file.
    Returns: (results, sources, utm_zone, background)

    results    : dict keyed by averaging period -> {"x", "y", "z"}
    sources    : dict keyed by source name -> (x, y) UTM
    utm_zone   : int, defaults to 12 if not found in file
    background : dict from parse_background()
    """
    path = Path(filepath)
    if not path.exists():
        st.error(f"File not found: {filepath}")
        return {}, {}, 12, {"present": False}

    text = path.read_text(errors="replace")

    # ── Sources ───────────────────────────────────────────────────────────────
    source_pattern = re.compile(
        r"LOCATION\s+(\w+)\s+POINT\s+([\d.\-]+)\s+([\d.\-]+)",
        re.IGNORECASE
    )
    sources = {
        m.group(1).capitalize(): (float(m.group(2)), float(m.group(3)))
        for m in source_pattern.finditer(text)
    }

    # ── UTM zone ──────────────────────────────────────────────────────────────
    utm_match = re.search(r"UTMZONE\s+(\d+)", text, re.IGNORECASE)
    utm_zone = int(utm_match.group(1)) if utm_match else 12

    # ── Background ────────────────────────────────────────────────────────────
    background = parse_background(text)

    # ── Concentration blocks ──────────────────────────────────────────────────
    block_pattern = re.compile(
        r"\*\*\*\s+THE\s+(ANNUAL|(?:\d+ST-HIGHEST\s+MAX\s+DAILY\s+)?1-HR)"
        r"\s+.*?CONCENTRATION.*?\*\*\*"
        r"(.*?)"
        r"(?=\*\*\*\s+THE\s+(?:ANNUAL|\d+ST)|\*\*\*\s+AERMOD\s+-\s+VERSION|\Z)",
        re.DOTALL | re.IGNORECASE
    )

    period_pages = {}
    for match in block_pattern.finditer(text):
        period_raw = match.group(1).upper()
        period = "ANNUAL" if "ANNUAL" in period_raw else "1-HR"

        x_vals, rows = parse_grid_table(match.group(2))
        if x_vals is None or not rows:
            continue

        if period not in period_pages:
            period_pages[period] = []
        period_pages[period].append((x_vals, rows))

    results = {}
    for period, pages in period_pages.items():
        x_vals, y_vals, Z = merge_pages(pages)
        results[period] = {"x": x_vals, "y": y_vals, "z": Z}

    return results, sources, utm_zone, background


# =========================
# HELPERS
# =========================

def auto_offsets(sources: dict) -> dict:
    """
    Generate annotation offsets based on each source position
    relative to the centroid. Nothing hardcoded.
    """
    if not sources:
        return {}
    xs = [v[0] for v in sources.values()]
    ys = [v[1] for v in sources.values()]
    cx, cy = np.mean(xs), np.mean(ys)
    return {
        name: (12 if sx >= cx else -118, 18 if sy >= cy else -28)
        for name, (sx, sy) in sources.items()
    }


def find_max_xy(x_vals, y_vals, Z):
    """UTM coordinates of the grid cell with the highest concentration."""
    idx = np.unravel_index(np.nanargmax(Z), Z.shape)
    return (x_vals[idx[1]], y_vals[idx[0]])


# =========================
# NAAQS STANDARDS
# Fixed federal regulatory values. Correct to hardcode.
# Units: µg/m³
# =========================

NAAQS = {
    "NO2": {
        "ANNUAL": (53.0,    "53 µg/m³ annual"),
        "1-HR":   (188.0,   "188 µg/m³ 1-hr (98th pct)"),
    },
    "SO2": {
        "ANNUAL": (None,    "No annual NAAQS for SO2"),
        "1-HR":   (196.0,   "196 µg/m³ 1-hr (99th pct)"),
    },
    "CO": {
        "ANNUAL": (None,    "No annual NAAQS for CO"),
        "1-HR":   (40000.0, "40,000 µg/m³ 1-hr"),
    },
    "PM2.5": {
        "ANNUAL": (15.0,   "15 µg/m³ annual"),
        "1-HR":   (None,   "No 1-hr standard — use 24-hr"),
    },
    "PM10": {
        "ANNUAL": (None,   "No annual NAAQS for PM10"),
        "1-HR":   (150.0,  "150 µg/m³ 24-hr"),
    },
}


# =========================
# PLOTS
# =========================

def make_plume_figure(x_vals, y_vals, Z, title, cmap,
                      max_label, max_xy, sources, label_offsets,
                      utm_zone=12):
    utm_proj = ccrs.UTM(zone=utm_zone, southern_hemisphere=False)
    XX, YY = np.meshgrid(x_vals, y_vals)

    fig, ax = plt.subplots(figsize=(9, 7.8), subplot_kw={"projection": utm_proj})

    try:
        ax.add_image(cimgt.OSM(), 15)
    except Exception:
        ax.set_facecolor("#e8f4f8")

    pad = 200
    ax.set_extent(
        [x_vals.min() - pad, x_vals.max() + pad,
         y_vals.min() - pad, y_vals.max() + pad],
        crs=utm_proj
    )
    ax.add_feature(
        cfeature.STATES.with_scale("10m"),
        linewidth=0.8, edgecolor="dimgray", facecolor="none"
    )

    nlevels = 14
    cf = ax.contourf(XX, YY, Z, levels=nlevels, cmap=cmap,
                     alpha=0.65, transform=utm_proj)
    ax.contour(XX, YY, Z, levels=nlevels,
               colors="white", linewidths=0.4, alpha=0.5, transform=utm_proj)

    for name, (sx, sy) in sources.items():
        ax.scatter(sx, sy, s=60, marker="^", color="black",
                   zorder=6, transform=utm_proj)
        dx, dy = label_offsets.get(name, (12, 18))
        ax.annotate(
            name,
            xy=(sx, sy), xytext=(sx + dx, sy + dy),
            fontsize=8.5, fontweight="bold",
            bbox=dict(facecolor="white", alpha=0.80, edgecolor="none", pad=1.5),
            transform=utm_proj, zorder=7,
            arrowprops=dict(arrowstyle="-", color="black", lw=0.6, alpha=0.7)
        )

    mx, my = max_xy
    ax.scatter(mx, my, s=140, marker="*", color="cyan",
               edgecolors="black", linewidths=0.7, zorder=8,
               transform=utm_proj, label=max_label)
    ax.legend(loc="lower right", fontsize=9, framealpha=0.85, edgecolor="grey")

    gl = ax.gridlines(draw_labels=True, linewidth=0.5,
                      color="gray", alpha=0.5, linestyle="--")
    gl.top_labels   = False
    gl.right_labels = False
    gl.xlabel_style = {"size": 8}
    gl.ylabel_style = {"size": 8}

    cbar = fig.colorbar(cf, ax=ax, pad=0.02, fraction=0.036, shrink=0.85)
    cbar.set_label("Concentration (µg m$^{-3}$)", fontsize=11)
    cbar.ax.tick_params(labelsize=10)

    ax.set_title(title, fontsize=13, pad=10, fontweight="bold")
    plt.tight_layout()
    return fig


def plot_hourly_background(values):
    fig, ax = plt.subplots(figsize=(3.5, 2.2))
    ax.plot(range(24), values,
            color="steelblue", linewidth=1.5, marker="o", markersize=3)
    ax.set_xlabel("Hour (UTC)", fontsize=8)
    ax.set_ylabel("µg/m³", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    return fig


# =========================
# APP
# =========================

st.set_page_config(page_title="AERMOD Plume Dashboard", layout="wide")
st.title("AERMOD Dispersion Dashboard")
st.caption(
    "Parses any AERMOD .out file directly. "
    "Sources, UTM zone, background, and max receptor are all read from the file."
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.header("Configuration")

out_file = st.sidebar.text_input(
    "Path to AERMOD .out file",
    value=r"run2.out"
)

pollutant = st.sidebar.selectbox(
    "Pollutant (for NAAQS comparison)",
    list(NAAQS.keys()),
    index=0
)

# ── Parse ─────────────────────────────────────────────────────────────────────
data, sources, utm_zone, background = parse_aermod_out(out_file)

if not data:
    st.error(
        "Could not parse the AERMOD .out file. "
        "Check the file path in the sidebar and confirm the run completed successfully."
    )
    st.stop()

label_offsets = auto_offsets(sources)

if not sources:
    st.warning("No LOCATION keywords found in .out file. Source markers will not be shown.")

# ── Sidebar info ──────────────────────────────────────────────────────────────
if sources:
    st.sidebar.divider()
    st.sidebar.subheader("Sources parsed from file")
    for name, (sx, sy) in sources.items():
        st.sidebar.write(f"**{name}**: ({sx:.1f}, {sy:.1f})")

st.sidebar.divider()
st.sidebar.caption(f"UTM Zone: {utm_zone}")
st.sidebar.caption(
    f"Background: {'Yes (' + background['type'] + ')' if background['present'] else 'None'}"
)

# ── Plot configs — everything from parsed data ────────────────────────────────
plot_configs = {}
for period in ["ANNUAL", "1-HR"]:
    if period not in data:
        continue
    z = data[period]["z"]
    x = data[period]["x"]
    y = data[period]["y"]
    plot_configs[period] = {
        "title":     f"{period} Concentration Plume",
        "cmap":      "YlOrRd" if period == "ANNUAL" else "plasma",
        "max_label": f"Max: {np.nanmax(z):.2f} µg m⁻³",
        "max_xy":    find_max_xy(x, y, z),
    }

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_labels = list(plot_configs.keys())
tabs = st.tabs(tab_labels)

for tab, period in zip(tabs, tab_labels):
    with tab:
        d   = data[period]
        cfg = plot_configs[period]
        z   = d["z"]

        col1, col2 = st.columns([3, 1])

        with col1:
            with st.spinner("Rendering plume map..."):
                fig = make_plume_figure(
                    x_vals=d["x"], y_vals=d["y"], Z=z,
                    title=cfg["title"], cmap=cfg["cmap"],
                    max_label=cfg["max_label"], max_xy=cfg["max_xy"],
                    sources=sources, label_offsets=label_offsets,
                    utm_zone=utm_zone
                )
                st.pyplot(fig)

                buf = io.BytesIO()
                fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
                buf.seek(0)
                st.download_button(
                    label="Download PNG",
                    data=buf,
                    file_name=f"aermod_{period.lower().replace('-','_')}.png",
                    mime="image/png"
                )
                plt.close(fig)

        with col2:
            st.subheader("Grid Stats")
            st.metric("Max (µg/m³)",  f"{np.nanmax(z):.2f}")
            st.metric("Mean (µg/m³)", f"{np.nanmean(z):.2f}")
            st.metric("Min (µg/m³)",  f"{np.nanmin(z):.2f}")

            st.divider()

            st.subheader("NAAQS")
            naaqs_entry = NAAQS.get(pollutant, {}).get(period)
            if naaqs_entry and naaqs_entry[0] is not None:
                threshold, label = naaqs_entry
                pct = np.nanmax(z) / threshold * 100
                st.metric("Standard", label)
                st.metric(
                    "Max as % of NAAQS",
                    f"{pct:.1f}%",
                    delta="EXCEEDS standard" if pct >= 100 else "below standard",
                    delta_color="inverse" if pct >= 100 else "normal"
                )
            else:
                _, label = naaqs_entry if naaqs_entry else (None, "No standard for this period")
                st.info(label)

            st.divider()

            st.subheader("Background")
            if background["present"]:
                st.metric("Type",         background["type"])
                st.metric("Mean (µg/m³)", f"{background['mean']:.1f}")
                st.metric("Max (µg/m³)",  f"{background['max']:.1f}")
                st.metric("Min (µg/m³)",  f"{background['min']:.1f}")

                if naaqs_entry and naaqs_entry[0] is not None:
                    bg_pct   = background["max"] / naaqs_entry[0] * 100
                    headroom = naaqs_entry[0] - np.nanmax(z)
                    st.metric("Max background as % of NAAQS", f"{bg_pct:.1f}%")
                    st.metric(
                        "Remaining NAAQS headroom",
                        f"{headroom:.2f} µg/m³",
                        delta="over limit" if headroom < 0 else "available",
                        delta_color="inverse" if headroom < 0 else "normal"
                    )

                if background["type"] == "HROFDY" and len(background["values"]) == 24:
                    st.markdown("**Hourly background profile**")
                    fig_bg = plot_hourly_background(background["values"])
                    st.pyplot(fig_bg)
                    plt.close(fig_bg)
            else:
                st.info("No background concentrations in this run.")

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    f"Model: EPA AERMOD | "
    f"Pollutant: {pollutant} | "
    f"UTM Zone: {utm_zone}N | "
    f"Background: {'Yes (' + background['type'] + ')' if background['present'] else 'None'} | "
    f"Sources: {', '.join(sources.keys()) if sources else 'none detected'}"
)
