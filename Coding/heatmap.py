import gc
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from scipy.ndimage import gaussian_filter

# Dark-to-bright custom colormap:
#   near-zero → very dark navy  (no activity = absence)
#   mid       → deep purple
#   high      → orange → yellow (heat progression)
_SCOUT_CMAP = LinearSegmentedColormap.from_list(
    'scout_heat',
    ['#0d0221', '#1b1464', '#7b2d8b', '#f77f00', '#fcbf49', '#ffffff'],
    N=256,
)

def save_heatmap(points, stats, player_name, positions, team_name, png_path):
    grid = np.zeros((100, 100))
    for p in points:
        x = min(int(p["x"]), 99)
        y = min(int(p["y"]), 99)
        if 0 <= x <= 99 and 0 <= y <= 99:
            grid[y, x] += p["count"]

    grid_smooth  = gaussian_filter(grid, sigma=4)
    position_str = ", ".join(positions) if positions else "N/A"

    # Mask truly-empty cells so the dark background shows through rather than
    # the lowest colormap colour bleeding across the whole pitch.
    masked = np.ma.masked_where(grid_smooth < 0.05 * grid_smooth.max() if grid_smooth.max() > 0 else grid_smooth < 0.05, grid_smooth)

    fig, ax = plt.subplots(figsize=(12, 8))
    fig.patch.set_facecolor("#0d0221")
    ax.set_facecolor("#0d0221")
    hm = ax.imshow(masked, origin="lower", extent=[0, 100, 0, 100],
                   cmap=_SCOUT_CMAP, vmin=0, alpha=0.92, aspect="auto")
    cbar = plt.colorbar(hm, ax=ax, label="Activity density")
    cbar.ax.yaxis.label.set_color("white")
    cbar.ax.tick_params(colors="white")

    lc, lw = "white", 1.5
    ax.add_patch(patches.Rectangle((0, 0),       100,  100,  fill=False, edgecolor=lc, linewidth=lw))
    ax.axvline(50, color=lc, linewidth=lw)
    ax.add_patch(plt.Circle((50, 50), 9.15, color=lc, fill=False, linewidth=lw))
    ax.plot(50, 50, "o", color=lc, markersize=3)
    ax.add_patch(patches.Rectangle((0, 21.1),    16.5, 57.8, fill=False, edgecolor=lc, linewidth=lw))
    ax.add_patch(patches.Rectangle((83.5, 21.1), 16.5, 57.8, fill=False, edgecolor=lc, linewidth=lw))
    ax.add_patch(patches.Rectangle((0, 36.8),    5.5,  26.4, fill=False, edgecolor=lc, linewidth=lw))
    ax.add_patch(patches.Rectangle((94.5, 36.8), 5.5,  26.4, fill=False, edgecolor=lc, linewidth=lw))
    ax.add_patch(patches.Rectangle((-2, 44.2),   2,    11.6, fill=False, edgecolor=lc, linewidth=lw))
    ax.add_patch(patches.Rectangle((100, 44.2),  2,    11.6, fill=False, edgecolor=lc, linewidth=lw))
    ax.plot(11, 50, "o", color=lc, markersize=3)
    ax.plot(89, 50, "o", color=lc, markersize=3)
    ax.set_xlim(-3, 103)
    ax.set_ylim(-3, 103)
    ax.axis("off")
    ax.set_title(
        f"{player_name} ({position_str}) — {team_name}\n"
        f"Apps: {stats.get('appearances','?')}  |  Goals: {stats.get('goals','?')}  |  "
        f"Assists: {stats.get('assists','?')}  |  Rating: {stats.get('rating', 0):.2f}",
        color="white", fontsize=13, pad=12
    )
    plt.tight_layout()
    plt.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    gc.collect()