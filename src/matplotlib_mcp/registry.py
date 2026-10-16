"""The explicit Matplotlib call surface exposed by the server."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Method:
    summary: str
    args: str
    kwargs: tuple[str, ...]
    example: str


# These are native Axes methods. Calls are dispatched only through this table.
PLOT_METHODS: dict[str, Method] = {
    "plot": Method("Plot lines and markers.", "x, y[, fmt]", ("drawstyle", "markersize", "marker", "markerfacecolor", "markeredgecolor", "color", "label", "linewidth", "linestyle", "alpha", "zorder", "rasterized"), 'plot(x, y, "o-")'),
    "scatter": Method("Draw a scatter or bubble plot.", "x, y", ("s", "c", "cmap", "norm", "vmin", "vmax", "marker", "edgecolors", "linewidths", "color", "label", "alpha", "zorder", "rasterized"), "scatter(x, y, s=size, c=value, cmap='viridis')"),
    "bar": Method("Draw vertical bars.", "x, height", ("width", "bottom", "align", "tick_label", "error_kw", "capsize", "color", "edgecolor", "linewidth", "label", "alpha", "zorder", "rasterized"), "bar(x, height, width=0.8)"),
    "barh": Method("Draw horizontal bars.", "y, width", ("height", "left", "align", "tick_label", "error_kw", "capsize", "color", "edgecolor", "linewidth", "label", "alpha", "zorder", "rasterized"), "barh(y, width)") ,
    "step": Method("Draw a step plot.", "x, y", ("where", "color", "label", "linewidth", "linestyle", "alpha"), "step(x, y, where='mid')"),
    "stem": Method("Draw a stem plot.", "[x,] y", ("linefmt", "markerfmt", "basefmt", "bottom", "orientation"), "stem(x, y)"),
    "stairs": Method("Draw a stepwise constant function.", "values[, edges]", ("orientation", "baseline", "fill", "color", "label", "linewidth", "linestyle", "alpha"), "stairs(values, edges)"),
    "fill": Method("Fill a polygon.", "x, y", ("closed", "color", "alpha", "label"), "fill(x, y, alpha=0.3)"),
    "fill_between": Method("Fill the area between two curves.", "x, y1, y2", ("where", "interpolate", "step", "color", "facecolor", "edgecolor", "alpha", "label"), "fill_between(x, low, high, alpha=0.2)"),
    "fill_betweenx": Method("Fill between curves along the y axis.", "y, x1, x2", ("where", "interpolate", "step", "color", "facecolor", "edgecolor", "alpha", "label"), "fill_betweenx(y, left, right)") ,
    "stackplot": Method("Draw a stacked area plot.", "x, y1, y2, ...", ("baseline", "labels", "colors", "alpha"), "stackplot(x, a, b, labels=['A', 'B'])"),
    "hist": Method("Draw a histogram.", "x", ("bins", "range", "density", "weights", "cumulative", "histtype", "align", "orientation", "rwidth", "log", "stacked", "color", "label", "edgecolor", "linewidth", "alpha", "zorder", "rasterized"), "hist(values, bins=20)"),
    "boxplot": Method("Draw a box-and-whisker plot.", "x", ("notch", "sym", "vert", "orientation", "whis", "positions", "widths", "patch_artist", "showmeans", "showcaps", "showbox", "showfliers", "manage_ticks", "tick_labels"), "boxplot([group_a, group_b])"),
    "violinplot": Method("Draw a violin plot.", "dataset", ("positions", "vert", "orientation", "widths", "showmeans", "showextrema", "showmedians", "quantiles", "points", "bw_method", "side"), "violinplot([group_a, group_b])"),
    "eventplot": Method("Draw positions of discrete events.", "positions", ("orientation", "lineoffsets", "linelengths", "linewidths", "colors", "linestyles"), "eventplot([times_a, times_b])"),
    "ecdf": Method("Draw an empirical cumulative distribution function.", "x", ("weights", "complementary", "compress", "color"), "ecdf(values, color='#003B71')"),
    "errorbar": Method("Plot y values with x/y error ranges.", "x, y", ("color", "xerr", "yerr", "fmt", "ecolor", "elinewidth", "capsize", "barsabove", "lolims", "uplims", "xlolims", "xuplims"), "errorbar(x, y, yerr=error, fmt='o', color='#003B71')"),
    "hist2d": Method("Draw a bivariate histogram.", "x, y", ("bins", "range", "density", "weights", "cmin", "cmax", "cmap", "norm", "vmin", "vmax"), "hist2d(x, y, bins=30)"),
    "hexbin": Method("Draw a hexagonal binning plot.", "x, y", ("C", "gridsize", "bins", "xscale", "yscale", "extent", "mincnt", "marginals", "cmap", "norm", "vmin", "vmax"), "hexbin(x, y, gridsize=30)"),
    "pie": Method("Draw a pie chart (use wedgeprops={'width': ...} for a donut).", "x", ("explode", "labels", "colors", "autopct", "pctdistance", "shadow", "labeldistance", "startangle", "radius", "counterclock", "wedgeprops", "textprops", "center", "frame", "rotatelabels", "normalize", "hatch"), "pie(values, labels=labels, autopct='%1.1f%%')"),
    "imshow": Method("Display a 2D array as an image or heatmap.", "X", ("cmap", "norm", "aspect", "interpolation", "alpha", "vmin", "vmax", "origin", "extent", "filternorm", "filterrad", "resample"), "imshow(matrix, cmap='viridis')"),
    "pcolormesh": Method("Draw a pseudocolor mesh.", "C or X, Y, C", ("cmap", "norm", "vmin", "vmax", "shading", "edgecolors", "alpha", "antialiased", "snap"), "pcolormesh(x_grid, y_grid, matrix, shading='auto')"),
    "contour": Method("Draw contour lines.", "Z or X, Y, Z", ("levels", "colors", "cmap", "norm", "vmin", "vmax", "origin", "extent", "extend", "corner_mask", "algorithm", "nchunk", "linewidths", "linestyles"), "contour(x_grid, y_grid, matrix, levels=10)"),
    "contourf": Method("Draw filled contours.", "Z or X, Y, Z", ("levels", "colors", "cmap", "norm", "vmin", "vmax", "origin", "extent", "extend", "corner_mask", "algorithm", "nchunk", "antialiased"), "contourf(x_grid, y_grid, matrix, levels=10, cmap='viridis')"),
    "quiver": Method("Draw a vector field with arrows.", "X, Y, U, V[, C]", ("color", "units", "angles", "scale_units", "scale", "width", "headwidth", "headlength", "headaxislength", "minshaft", "minlength", "pivot", "cmap", "norm", "vmin", "vmax"), "quiver(x, y, u, v, color='#003B71')") ,
    "barbs": Method("Draw a vector field with barbs.", "X, Y, U, V[, C]", ("length", "pivot", "barbcolor", "flagcolor", "sizes", "fill_empty", "rounding", "barb_increments", "flip_barb", "cmap", "norm", "vmin", "vmax"), "barbs(x, y, u, v)"),
    "streamplot": Method("Draw streamlines for a vector field.", "x, y, u, v", ("density", "linewidth", "color", "cmap", "norm", "arrowsize", "arrowstyle", "minlength", "start_points", "maxlength", "integration_direction", "broken_streamlines"), "streamplot(x, y, u, v, density=1.2)"),
    "triplot": Method("Draw a triangulation mesh.", "x, y[, triangles]", ("mask", "color", "marker", "markersize", "linewidth", "linestyle"), "triplot(x, y)"),
    "tripcolor": Method("Draw a pseudocolor plot on a triangulation.", "x, y, c", ("triangles", "mask", "shading", "facecolors", "cmap", "norm", "vmin", "vmax", "alpha", "edgecolors", "linewidth", "antialiased"), "tripcolor(x, y, values, shading='gouraud')"),
    "tricontour": Method("Draw contour lines on an unstructured triangular grid.", "x, y, z", ("triangles", "mask", "levels", "colors", "cmap", "norm", "vmin", "vmax", "extend", "linewidths", "linestyles"), "tricontour(x, y, z, levels=10)"),
    "tricontourf": Method("Draw filled contours on a triangular grid.", "x, y, z", ("triangles", "mask", "levels", "colors", "cmap", "norm", "vmin", "vmax", "extend", "antialiased"), "tricontourf(x, y, z, levels=10)"),
}

SETUP_METHODS: dict[str, tuple[int, int, tuple[str, ...]]] = {
    "set_title": (1, 1, ("label", "fontdict", "loc", "pad", "y", "color")),
    "set_xlabel": (1, 1, ("xlabel", "fontdict", "labelpad", "loc", "color")),
    "set_ylabel": (1, 1, ("ylabel", "fontdict", "labelpad", "loc", "color")),
    "set_xlim": (0, 2, ("left", "right", "emit", "auto", "xmin", "xmax")),
    "set_ylim": (0, 2, ("bottom", "top", "emit", "auto", "ymin", "ymax")),
    "set_xscale": (1, 1, ("value", "base", "subs", "nonpositive")),
    "set_yscale": (1, 1, ("value", "base", "subs", "nonpositive")),
    "set_xticks": (1, 2, ("ticks", "labels", "minor")),
    "set_yticks": (1, 2, ("ticks", "labels", "minor")),
    "set_xticklabels": (1, 1, ("labels", "fontdict", "minor", "fontfamily", "fontsize", "color", "rotation")),
    "set_yticklabels": (1, 1, ("labels", "fontdict", "minor", "fontfamily", "fontsize", "color", "rotation")),
    "grid": (0, 0, ("visible", "which", "axis", "color", "linestyle", "linewidth", "alpha")),
    "legend": (0, 2, ("labels", "loc", "ncols", "fontsize", "frameon", "title", "alignment", "borderpad", "labelspacing", "handlelength", "handletextpad", "columnspacing", "markerscale", "markerfirst", "framealpha", "facecolor", "edgecolor", "mode", "bbox_to_anchor")),
    "axhline": (0, 1, ("y", "xmin", "xmax", "color")),
    "axvline": (0, 1, ("x", "ymin", "ymax", "color")),
    "axhspan": (2, 2, ("xmin", "xmax", "ymin", "ymax", "color")),
    "axvspan": (2, 2, ("ymin", "ymax", "xmin", "xmax", "color")),
}
