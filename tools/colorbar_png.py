# -*- coding: utf-8 -*-
"""
Creates a figure of a colorbar from the pysiral parameter definition

Created on Wed May 25 16:12:01 2016

@author: shendric
"""

from pysiral.config import get_parameter_definitions
from pysiral.visualization.mapstyle import get_custom_font

import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

import os


def colorbar_png():

    parser = get_argparser()
    args = parser.parse_args()

    # Get the parameter definition for the corresponding parameter
    parameter_definitions = get_parameter_definitions()
    pardef = parameter_definitions[args.parameter]

    if args.diff:
        cmap = pardef.cmap_diff
        parameter_label = "$\Delta$ " + pardef.label+" ("+pardef.unit+")"
        output_filename = os.path.join(
            args.output, "colorbar_%s.png" % args.parameter)
    else:
        cmap = pardef.cmap
        parameter_label = pardef.label+" ("+pardef.unit+")"
        output_filename = os.path.join(
            args.output, "colorbar_%s_diff.png" % args.parameter)

    font_label_properties = {
        "color": "#4b4b4b",
        "fontproperties": get_custom_font(fontsize=22, awi_font=False)}

    # Create the figure
    figsize = (8, 1.5)
    plt.figure(figsize=figsize)
    ax = plt.gca()
    ax.set_position([0.05, 0.6, 0.9, 0.3])

    sm = plt.cm.ScalarMappable(cmap=plt.get_cmap(cmap.name),
                               norm=plt.Normalize(vmin=cmap.vmin,
                                                  vmax=cmap.vmax))
    sm._A = []
#    cb_ax_kwargs = {
#        'loc': 3, 'bbox_to_anchor': (0.05, 0.75, 0.9, 0.25),
#        'width': "100%", 'height': "100%", 'bbox_transform': ax.transAxes,
#        'borderpad': 0}
#    ticks = MultipleLocator(cmap.step)
#    axins = inset_axes(ax, **cb_ax_kwargs)
    ticks = MultipleLocator(cmap.step)
    cb = plt.colorbar(sm, cax=ax, ticks=ticks, orientation="horizontal")
    cl = plt.getp(cb.ax, 'xmajorticklabels')
    plt.setp(cl, **font_label_properties)
    parameter_label = parameter_label
    cb.set_label(parameter_label, **font_label_properties)
    cb.outline.set_linewidth(0.1)
    cb.outline.set_alpha(0.0)
    for t in cb.ax.get_yticklines():
        t.set_color("1.0")
    cb.ax.tick_params('both', length=4.0, which='major', pad=10,
                      direction="out", color="#4b4b4b")
    plt.sca(ax)

    plt.savefig(output_filename, transparent=True, dpi=600)


def get_argparser():
    """ Handle command line arguments """
    import argparse

    parser = argparse.ArgumentParser()

    # Positional arguments
    parser.add_argument(
        action='store', dest='parameter',
        help='parameter name')

    parser.add_argument(
        action='store', dest='output',
        help='output folder')

    parser.add_argument(
        "-font",
        action='store', dest='font', default="awi-font",
        help='Font to use')

    # Batch Processing
    parser.add_argument(
        '--diff',
        action='store_true', dest='diff',
        help='positional argument is a search pattern')

    parser.add_argument(
        '--vertical',
        action='store_true', dest='vertical',
        help='create a vertical colorbar')

    # show preprocessor version
    parser.add_argument(
        '--version', action='version', version='%(prog)s 0.1a')

    return parser

if __name__ == "__main__":
    colorbar_png()
