"""Consistent signal colors and direction styles across live/exported plots."""
PALETTE=['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd','#8c564b','#e377c2','#17becf','#bcbd22','#7f7f7f',
         '#aec7e8','#ffbb78','#98df8a','#ff9896','#c5b0d5','#c49c94','#f7b6d2','#9edae5','#dbdb8d','#c7c7c7']


def signal_colors(rows):
    keys=dict.fromkeys((r['board'],r['testpoint'],r['unit']) for r in rows)
    return {key:PALETTE[i%len(PALETTE)] for i,key in enumerate(keys)}


def sweep_style(direction,color):
    down=direction=='down'
    return dict(color=color,linestyle=':' if down else '-',marker='v' if down else 'o',
                markerfacecolor='none' if down else color,markeredgecolor=color)
