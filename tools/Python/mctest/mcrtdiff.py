#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcrtdiff - compare per-instrument execution times across multiple mctest runs/builds.

mcrtdiff reads the ``testresults_<label>.json`` files that ``mctest``
(MCSTAS_TEST/MCXTEST) writes out - one per build/run - and compares the
per-instrument timing values, producing:

  * a total-runtime summary comparing builds (including absolute and
    relative differences vs the chosen reference), and
  * a per-instrument comparison with absolute and relative differences
    against a chosen reference build,

rendered both as a console table and as a self-contained HTML report.
The tool uses only the Python standard library (no jinja2/matplotlib/
mccodelib), so it can be run straight from the source tree.

Input format
------------
Each mctest run writes, inside its label directory:

    <testdir>/<label>/testresults_<label>.json

The JSON is a dictionary keyed by instrument display name, plus a special
"_meta" key holding run metadata (ncount, hostname, cpu_type, date, ...).
Each instrument entry contains the timing fields used here:

    runtime      seconds for the %Example test run      (default metric)
    compiletime  seconds spent compiling the instrument
    displaytime  seconds for the mcdisplay run

Values that are null, non-numeric, negative, or absent are treated as
"no data" and shown as "n/a". They are excluded from totals and from
difference calculations and are never treated as zero.

Usage
-----
    mcrtdiff.py [options] INPUT [INPUT ...]

INPUT may be any mix of:
  * a testdir root containing one subfolder per build (the mctest layout),
  * a single label directory holding a testresults_*.json file, or
  * a direct path to a testresults_*.json file.

If INPUT is omitted, the current directory is used.

Examples:
    mcrtdiff.py /path/to/mctest-results
    mcrtdiff.py build-a build-b
    mcrtdiff.py a/testresults_a.json b/testresults_b.json
    mcrtdiff.py --reflabel build-a --metric compiletime /path/to/results
"""
import argparse
import html
import json
import math
import os
import sys
from datetime import datetime

# timing fields mctest records per instrument (also used as --metric choices)
METRICS = ("runtime", "compiletime", "displaytime")

CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
     margin:24px;color:#222;background:#fff;}
h1{font-size:22px;margin-bottom:4px;}
h2{font-size:16px;margin-top:26px;border-bottom:1px solid #ccc;padding-bottom:4px;}
.tbl{border-collapse:collapse;margin:8px 0;font-size:13px;}
.tbl th,.tbl td{border:1px solid #ddd;padding:4px 8px;text-align:right;
                white-space:nowrap;}
.tbl th{background:#f4f4f4;text-align:center;}
.tbl td.instr{text-align:left;font-weight:600;}
tr.ref{background:#fffbe6;}
td.ref{background:#fffbe6;}
.good{color:#1a7f1a;font-weight:600;}
.bad{color:#b3261e;font-weight:600;}
.na{color:#999;}
.chart{margin:8px 0;}
.ci{display:flex;align-items:center;margin:3px 0;}
.cname{flex:0 0 220px;width:220px;font-size:12px;text-align:right;
       padding-right:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.bars{position:relative;flex:1 1 auto;}
.bar{display:inline-block;height:16px;margin-right:4px;line-height:16px;
     font-size:11px;color:#fff;padding:0 4px;border-radius:2px;box-sizing:border-box;
     min-width:18px;vertical-align:middle;white-space:nowrap;overflow:hidden;}
 .bar.good{background:#4caf50;}
 .bar.bad{background:#e57373;}
 .bar.na{background:#ccc;color:#555;}
 .bar.same{background:#90a4ae;}
.sw{display:inline-block;width:12px;height:12px;border-radius:2px;
    vertical-align:middle;margin:0 4px 0 12px;}
 .sw.good{background:#4caf50;}
 .sw.bad{background:#e57373;}
 .sw.na{background:#ccc;}
 .sw.same{background:#90a4ae;}
.legend{font-size:12px;color:#555;}
.gen{font-size:11px;color:#999;margin-top:26px;}
"""


def _label_from_filename(path):
    """Derive the build label from a testresults_*.json file name."""
    base = os.path.basename(path)
    if base.endswith(".json"):
        base = base[:-len(".json")]
    if base.startswith("testresults_"):
        base = base[len("testresults_"):]
    return base or os.path.splitext(os.path.basename(path))[0]


def _find_testresults_in_dir(d, recursive):
    """Return a list of (label, path) for testresults_*.json files under d."""
    out = []

    def scan(root):
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            return
        for e in entries:
            p = os.path.join(root, e)
            if os.path.isfile(p) and e.startswith("testresults_") and e.endswith(".json"):
                out.append((_label_from_filename(p), p))
            elif recursive and os.path.isdir(p):
                scan(p)

    scan(d)
    return out


def discover_builds(inputs):
    """Locate testresults JSON files for the given inputs.

    Returns an ordered list of (label, path) tuples, deduplicated by label
    (first occurrence wins). Inputs that do not exist are skipped with a
    warning printed to stderr.
    """
    found = {}
    order = []

    def add(label, path):
        if label and label not in found:
            found[label] = path
            order.append(label)

    for inp in inputs:
        if os.path.isfile(inp):
            if inp.endswith(".json"):
                add(_label_from_filename(inp), inp)
            else:
                print("WARNING: not a .json file, skipping: %s" % inp)
        elif os.path.isdir(inp):
            direct = _find_testresults_in_dir(inp, recursive=False)
            if direct:
                for label, path in direct:
                    add(label, path)
            else:
                for label, path in _find_testresults_in_dir(inp, recursive=True):
                    add(label, path)
        else:
            print("WARNING: input does not exist, skipping: %s" % inp)

    return [(l, found[l]) for l in order]


def load_build(label, path, warnings):
    """Load one build's testresults JSON into a dict, or None on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception as e:
        warnings.append("could not read %s: %s" % (path, e))
        return None
    if not isinstance(obj, dict):
        warnings.append("skipping %s (not a JSON object)" % path)
        return None
    meta = obj.get("_meta", {})
    if not isinstance(meta, dict):
        meta = {}
    tests = {k: v for k, v in obj.items() if k != "_meta" and isinstance(v, dict)}
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return {"label": label, "path": path, "meta": meta, "tests": tests, "mtime": mtime}


def metric_value(testobj, metric):
    """Return the numeric value of `metric` for a test object, or None.

    None is returned when the field is missing, non-numeric, negative, or
    not finite. This is the single place "no data" is decided so that
    totals and differences never silently treat missing data as zero.
    """
    if not isinstance(testobj, dict):
        return None
    v = testobj.get(metric)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        v = float(v)
        if not math.isfinite(v) or v < 0:
            return None
        return v
    return None


def build_total(build, metric):
    """Return (total, valid_count, test_count) for a build and metric."""
    total = 0.0
    valid = 0
    for tobj in build["tests"].values():
        v = metric_value(tobj, metric)
        if v is not None:
            total += v
            valid += 1
    return total, valid, len(build["tests"])


def build_total_value(build, metric):
    """Return the build's total as a comparison value for the given metric.

    Returns None when the build has no valid data for the metric, so a total
    backed by no data is treated like a missing value in delta calculations
    (never shown as a spurious 0.0).
    """
    total, valid, _n = build_total(build, metric)
    return total if valid > 0 else None


def _fmt_total(total, valid):
    """Format a build's total for display.

    Shows "n/a" when the build has zero valid values for the metric (the raw
    total would misleadingly read as 0.00); a genuine 0.0 total is retained
    whenever at least one value is valid.
    """
    return "n/a" if valid == 0 else ("%.2f" % total)


def all_instruments(builds):
    """Return the sorted union of instrument display names across builds."""
    keys = set()
    for b in builds:
        keys.update(b["tests"].keys())
    return sorted(keys)


def pick_reference(builds, reflabel, warnings):
    """Choose the reference build.

    Uses `reflabel` when given and present; otherwise the oldest build by
    file mtime (ties broken by label). Mirrors mcviewtest's "oldest" default.
    """
    if reflabel:
        for b in builds:
            if b["label"] == reflabel:
                return b
        warnings.append("--reflabel '%s' not found; using oldest build" % reflabel)
    return min(builds, key=lambda b: (b["mtime"], b["label"]))


def _fmt_val(v, unit="s"):
    return "n/a" if v is None else ("%.2f %s" % (v, unit))


def _delta_str(v, r):
    if v is None or r is None:
        return "n/a"
    return "%+.2f s" % (v - r)


def _rel_str(v, r):
    if v is None or r is None or abs(r) < 1e-9:
        return "n/a"
    return "%+.1f%%" % ((v - r) / abs(r) * 100.0)


def _delta_class(v, r):
    """CSS class for a build value relative to a reference value."""
    if v is None or r is None or abs(r) < 1e-9:
        return "na"
    rel = (v - r) / abs(r)
    if rel < -0.01:
        return "good"
    if rel > 0.01:
        return "bad"
    return "same"


def _short(s, n=28):
    return s if len(s) <= n else s[:n - 1] + "~"


def _sorted_keys(builds, ref, metric):
    """Instrument keys sorted by largest absolute difference vs reference."""
    keys = all_instruments(builds)
    refvals = {k: metric_value(ref["tests"].get(k, {}), metric) for k in keys}
    nonref = [b for b in builds if b is not ref]

    def mag(k):
        m = 0.0
        for b in nonref:
            v = metric_value(b["tests"].get(k, {}), metric)
            r = refvals[k]
            if v is not None and r is not None:
                m = max(m, abs(v - r))
        return -m

    return sorted(keys, key=mag), refvals, nonref


def render_text(builds, ref, metric, top):
    """Render the comparison as a plain-text console summary."""
    lines = []
    lines.append("=== McCode mctest %s comparison ===" % metric)
    lines.append("reference build: %s" % ref["label"])
    lines.append("")

    # total-runtime summary (with absolute/relative delta vs reference)
    tot_hdr = "total[%s](s)" % metric
    wt = max(len(tot_hdr), 8) + 1  # +1 guarantees a trailing gap before the next column
    lines.append(
        "build".ljust(24)
        + tot_hdr.ljust(wt)
        + "valid/total".ljust(12)
        + "d[s]".ljust(10)
        + "d[%]"
    )
    ref_total = build_total_value(ref, metric)
    for b in builds:
        total, valid, n = build_total(b, metric)
        val = build_total_value(b, metric)
        marker = " (ref)" if b is ref else ""
        lines.append(
            _short(b["label"], 24).ljust(24)
            + _fmt_total(total, valid).ljust(wt)
            + ("%d/%d" % (valid, n)).ljust(12)
            + _delta_str(val, ref_total).ljust(10)
            + _rel_str(val, ref_total)
            + marker
        )
    lines.append("")

    # per-instrument table
    lines.append("per-instrument %s (delta vs reference, sorted by change):" % metric)
    keys, refvals, nonref = _sorted_keys(builds, ref, metric)
    if top:
        keys = keys[:top]

    hdr = _short("instrument", 24).ljust(24)
    hdr += _short(ref["label"], 12).ljust(12)
    for b in nonref:
        hdr += _short(b["label"], 12).ljust(12) + "d[s]".ljust(9) + "d[%]".ljust(9)
    lines.append(hdr)

    for k in keys:
        rv = refvals[k]
        line = _short(k, 24).ljust(24)
        line += _fmt_val(rv).ljust(12)
        for b in nonref:
            v = metric_value(b["tests"].get(k, {}), metric)
            line += _fmt_val(v).ljust(12) + _delta_str(v, rv).ljust(9) + _rel_str(v, rv).ljust(9)
        lines.append(line)

    return "\n".join(lines)


def render_html(builds, ref, metric, title, warnings):
    """Render the comparison as a self-contained HTML report string."""
    esc = html.escape
    order = [ref] + [b for b in builds if b is not ref]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    keys, refvals, nonref = _sorted_keys(builds, ref, metric)

    p = []
    p.append("<!DOCTYPE html>")
    p.append('<html><head><meta charset="utf-8">')
    p.append("<title>%s</title>" % esc(title))
    p.append("<style>%s</style>" % CSS)
    p.append("</head><body>")
    p.append("<h1>%s</h1>" % esc(title))
    p.append(
        '<p>metric = <b>%s</b>, reference = <b>%s</b>, %d build(s), generated %s</p>'
        % (esc(metric), esc(ref["label"]), len(builds), esc(now))
    )

    # builds + totals table
    p.append("<h2>Builds and total %s</h2>" % esc(metric))
    p.append('<table class="tbl">')
    p.append(
        "<tr><th>build</th><th>date</th><th>host</th><th>cpu</th><th>gpu</th>"
        "<th>ncount</th><th>total %s [s]</th><th>valid/total</th>"
        "<th>&Delta; [s]</th><th>&Delta; [%%]</th></tr>" % esc(metric)
    )
    ref_total = build_total_value(ref, metric)
    for b in order:
        total, valid, n = build_total(b, metric)
        val = build_total_value(b, metric)
        m = b["meta"]
        trcls = ' class="ref"' if b is ref else ""
        dcls = _delta_class(val, ref_total)
        p.append("<tr%s>" % trcls)
        p.append(
            "<td>%s%s</td>"
            % (esc(b["label"]), ' <em>(ref)</em>' if b is ref else "")
        )
        p.append("<td>%s</td>" % esc(str(m.get("date", ""))))
        p.append("<td>%s</td>" % esc(str(m.get("hostname", ""))))
        p.append("<td>%s</td>" % esc(str(m.get("cpu_type", ""))))
        p.append("<td>%s</td>" % esc(str(m.get("gpu_type", ""))))
        p.append("<td>%s</td>" % esc(str(m.get("ncount", ""))))
        p.append("<td>%s</td>" % _fmt_total(total, valid))
        p.append("<td>%d/%d</td>" % (valid, n))
        p.append('<td class="%s">%s</td>' % (dcls, esc(_delta_str(val, ref_total))))
        p.append('<td class="%s">%s</td>' % (dcls, esc(_rel_str(val, ref_total))))
        p.append("</tr>")
    p.append("</table>")

    # per-instrument table
    p.append("<h2>Per-instrument %s</h2>" % esc(metric))
    p.append('<table class="tbl">')
    cells = ["<th>instrument</th>", '<th class="ref">%s (ref)</th>' % esc(ref["label"])]
    for b in nonref:
        cells.append("<th>%s</th>" % esc(b["label"]))
        cells.append("<th>&Delta; [s]</th>")
        cells.append("<th>&Delta; [%]</th>")
    p.append("<tr>" + "".join(cells) + "</tr>")
    for k in keys:
        rv = refvals[k]
        cells = ['<td class="instr">%s</td>' % esc(k), '<td class="ref">%s</td>' % _fmt_val(rv)]
        for b in nonref:
            v = metric_value(b["tests"].get(k, {}), metric)
            cls = _delta_class(v, rv)
            cells.append("<td>%s</td>" % _fmt_val(v))
            cells.append('<td class="%s">%s</td>' % (cls, esc(_delta_str(v, rv))))
            cells.append('<td class="%s">%s</td>' % (cls, esc(_rel_str(v, rv))))
        p.append("<tr>" + "".join(cells) + "</tr>")
    p.append("</table>")

    # bar chart (scaled per-instrument to the max build value)
    p.append("<h2>Bar chart (per-instrument, scaled to each instrument's max)</h2>")
    p.append('<div class="chart">')
    for k in keys:
        vals = [metric_value(b["tests"].get(k, {}), metric) for b in order]
        rv = refvals[k]
        present = [v for v in vals if v is not None]
        mx = max(present) if present else 0.0
        mx = mx if mx > 0 else 1.0
        p.append('<div class="ci">')
        p.append('<span class="cname" title="%s">%s</span>' % (esc(k), esc(_short(k))))
        p.append('<span class="bars">')
        for b, v in zip(order, vals):
            if v is None:
                p.append('<span class="bar na" title="%s: n/a">-</span>' % esc(b["label"]))
            else:
                w = max(1.0, v / mx * 100.0)
                cls = _delta_class(v, rv)
                p.append(
                    '<span class="bar %s" style="width:%.1f%%" title="%s: %.3f s">%s</span>'
                    % (cls, w, esc(b["label"]), v, esc("%.2f" % v))
                )
        p.append("</span></div>")
    p.append("</div>")
    p.append(
        '<p class="legend">'
        '<span class="sw good"></span>faster than ref '
        '<span class="sw bad"></span>slower than ref '
        '<span class="sw same"></span>same as ref '
        '<span class="sw na"></span>no data</p>'
    )

    if warnings:
        p.append("<h2>Warnings</h2><ul>")
        for w in warnings:
            p.append("<li>%s</li>" % esc(w))
        p.append("</ul>")

    p.append("<h2>Notes</h2><ul>")
    p.append(
        "<li>Values are <b>%s</b> recorded by <code>mctest</code> (seconds).</li>"
        % esc(metric)
    )
    p.append(
        "<li><b>n/a</b> means the value was missing, null, or negative in the "
        "source JSON; it is excluded from totals and differences (never zero).</li>"
    )
    p.append(
        "<li>&Delta; [s] = build &minus; reference; "
        "&Delta; [%] = (build &minus; reference) / reference &times; 100.</li>"
    )
    p.append(
        "<li>Bars are scaled per-instrument to the largest build value for that "
        "instrument, so within-instrument speedups/slowdowns are easy to read.</li>"
    )
    p.append(
        "<li>The total-runtime table shows each build's &Delta; vs the reference "
        "total (n/a when a build has no valid data for the metric).</li>"
    )
    p.append(
        "<li>Neutral (blue-grey) bars mark builds equal to the reference within "
        "1%, including the reference itself.</li>"
    )
    p.append("</ul>")
    p.append('<p class="gen">generated by mcrtdiff (McCode)</p>')
    p.append("</body></html>")
    return "\n".join(p)


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="testdir root, label dir(s), or testresults_*.json file(s); PWD if omitted",
    )
    parser.add_argument("--reflabel", help="reference build label (default: oldest by mtime)")
    parser.add_argument(
        "--metric",
        choices=METRICS,
        default="runtime",
        help="timing field to compare (default: runtime)",
    )
    parser.add_argument("--output", help="output HTML file (default: ./mcrtdiff_report.html)")
    parser.add_argument("--title", help="report title")
    parser.add_argument(
        "--top", type=int, help="show only the top-N most-changed instruments in the console table"
    )
    parser.add_argument("--nobrowse", action="store_true", help="do not open the report in a browser")
    args = parser.parse_args(argv)

    inputs = args.inputs or [os.getcwd()]
    found = discover_builds(inputs)
    if not found:
        print("ERROR: no testresults_*.json files found in: %s" % ", ".join(inputs))
        return 1

    warnings = []
    builds = []
    for label, path in found:
        b = load_build(label, path, warnings)
        if b is not None:
            builds.append(b)
    if not builds:
        print("ERROR: no usable build data found.")
        for w in warnings:
            print("  " + w)
        return 1
    if len(builds) < 2:
        warnings.append("only one build found; comparison is trivial")

    ref = pick_reference(builds, args.reflabel, warnings)
    title = args.title or ("McCode mctest %s comparison" % args.metric)

    print(render_text(builds, ref, args.metric, args.top))
    outpath = args.output or os.path.join(os.getcwd(), "mcrtdiff_report.html")
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(render_html(builds, ref, args.metric, title, warnings))
    print("\nwrote report: %s" % outpath)
    for w in warnings:
        print("WARNING: %s" % w)

    if not args.nobrowse:
        try:
            import webbrowser

            webbrowser.open(os.path.abspath(outpath))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
