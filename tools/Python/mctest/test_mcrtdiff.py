#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fixture-based tests for mcrtdiff (parsing, aggregation, comparison, rendering).

Run with either:
    python3 -m unittest tools.Python.mctest.test_mcrtdiff   (from repo root)
    python3 tools/Python/mctest/test_mcrtdiff.py            (standalone)

The tests build synthetic testresults_*.json files on disk (in a temp dir)
matching the format mctest writes, so they exercise the real discovery /
parsing / aggregation / comparison / rendering code paths end to end.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcrtdiff


def _testobj(runtime, compiletime=1.0, displaytime=0.5, testval=100.0, targetval=100.0):
    return {
        "displayname": None,  # set by caller
        "instrname": None,
        "testnb": 1,
        "runtime": runtime,
        "compiletime": compiletime,
        "displaytime": displaytime,
        "testval": testval,
        "targetval": targetval,
        "compiled": True,
        "didrun": True,
    }


def _write_build(root, label, instrs, meta, mtime):
    """Write <root>/<label>/testresults_<label>.json for a synthetic build."""
    d = os.path.join(root, label)
    os.makedirs(d)
    obj = {}
    for name, spec in instrs.items():
        to = _testobj(**spec) if isinstance(spec, dict) else _testobj(spec)
        to["displayname"] = name
        to["instrname"] = name
        obj[name] = to
    obj["_meta"] = meta
    path = os.path.join(d, "testresults_%s.json" % label)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.utime(path, (mtime, mtime))
    return path


def _meta(host, cpu, date):
    return {
        "ncount": "1e6",
        "mpi": None,
        "date": date,
        "hostname": host,
        "user": "tester",
        "cpu_type": cpu,
        "gpu_type": "none",
    }


class _Fixtures(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mcrtdiff_test_")
        # build A (older, reference by mtime): instrA=10.0, instrB missing
        _write_build(
            self.root,
            "buildA",
            {
                "instrA": {"runtime": 10.0},
                "instrB": {"runtime": None},
            },
            _meta("hostA", "CPU-A", "2026-01-01 00:00:00"),
            mtime=1000.0,
        )
        # build B (newer): instrA=15.0, instrB=5.0 (now present), instrC=8.0 (extra)
        _write_build(
            self.root,
            "buildB",
            {
                "instrA": {"runtime": 15.0},
                "instrB": {"runtime": 5.0},
                "instrC": {"runtime": 8.0},
            },
            _meta("hostB", "CPU-B", "2026-01-02 00:00:00"),
            mtime=2000.0,
        )
        self.warns = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _builds(self, inputs=None):
        inputs = inputs or [self.root]
        found = mcrtdiff.discover_builds(inputs)
        builds = []
        for label, path in found:
            b = mcrtdiff.load_build(label, path, self.warns)
            if b is not None:
                builds.append(b)
        return builds


class TestParsing(_Fixtures):
    def test_label_from_filename(self):
        self.assertEqual(
            mcrtdiff._label_from_filename("/x/y/testresults_foo-bar.json"), "foo-bar"
        )

    def test_discover_root_finds_two_builds(self):
        found = mcrtdiff.discover_builds([self.root])
        self.assertEqual({l for l, _ in found}, {"buildA", "buildB"})

    def test_discover_direct_files(self):
        pa = os.path.join(self.root, "buildA", "testresults_buildA.json")
        pb = os.path.join(self.root, "buildB", "testresults_buildB.json")
        found = mcrtdiff.discover_builds([pa, pb])
        self.assertEqual({l for l, _ in found}, {"buildA", "buildB"})

    def test_discover_label_dir(self):
        found = mcrtdiff.discover_builds([os.path.join(self.root, "buildA")])
        self.assertEqual({l for l, _ in found}, {"buildA"})

    def test_discover_missing_input_skipped(self):
        found = mcrtdiff.discover_builds(["/no/such/path/xyz123"])
        self.assertEqual(found, [])

    def test_load_build_parses_meta_and_tests(self):
        builds = self._builds()
        a = [b for b in builds if b["label"] == "buildA"][0]
        self.assertEqual(a["meta"]["hostname"], "hostA")
        self.assertIn("instrA", a["tests"])
        self.assertNotIn("_meta", a["tests"])

    def test_load_build_corrupt(self):
        d = os.path.join(self.root, "bad")
        os.makedirs(d)
        p = os.path.join(d, "testresults_bad.json")
        with open(p, "w") as f:
            f.write("{ not valid json")
        self.warns = []
        self.assertIsNone(mcrtdiff.load_build("bad", p, self.warns))
        self.assertTrue(self.warns)


class TestMetricValue(_Fixtures):
    def test_valid(self):
        self.assertEqual(mcrtdiff.metric_value({"runtime": 3.5}, "runtime"), 3.5)

    def test_missing(self):
        self.assertIsNone(mcrtdiff.metric_value({"compiletime": 1.0}, "runtime"))

    def test_null(self):
        self.assertIsNone(mcrtdiff.metric_value({"runtime": None}, "runtime"))

    def test_negative(self):
        self.assertIsNone(mcrtdiff.metric_value({"runtime": -1}, "runtime"))

    def test_string(self):
        self.assertIsNone(mcrtdiff.metric_value({"runtime": "5.0"}, "runtime"))

    def test_nan(self):
        self.assertIsNone(mcrtdiff.metric_value({"runtime": float("nan")}, "runtime"))

    def test_bool_not_a_number(self):
        self.assertIsNone(mcrtdiff.metric_value({"runtime": True}, "runtime"))

    def test_zero_is_valid(self):
        self.assertEqual(mcrtdiff.metric_value({"runtime": 0.0}, "runtime"), 0.0)


class TestAggregation(_Fixtures):
    def test_totals_exclude_missing(self):
        builds = self._builds()
        a = [b for b in builds if b["label"] == "buildA"][0]
        total, valid, n = mcrtdiff.build_total(a, "runtime")
        self.assertEqual(total, 10.0)   # instrB missing -> excluded
        self.assertEqual(valid, 1)
        self.assertEqual(n, 2)

    def test_total_all_present(self):
        builds = self._builds()
        b = [b for b in builds if b["label"] == "buildB"][0]
        total, valid, n = mcrtdiff.build_total(b, "runtime")
        self.assertEqual(total, 28.0)   # 15 + 5 + 8
        self.assertEqual(valid, 3)
        self.assertEqual(n, 3)

    def test_total_value(self):
        b_valid = {"label": "x", "path": "", "meta": {}, "tests": {"k": {"runtime": 3.0}}, "mtime": 0.0}
        b_nodata = {"label": "y", "path": "", "meta": {}, "tests": {"k": {"runtime": None}}, "mtime": 0.0}
        self.assertEqual(mcrtdiff.build_total_value(b_valid, "runtime"), 3.0)
        # a total backed by no valid data is treated as missing (None), not 0.0
        self.assertIsNone(mcrtdiff.build_total_value(b_nodata, "runtime"))

    def test_fmt_total(self):
        # zero valid values -> total shown as n/a (never a spurious 0.00)
        self.assertEqual(mcrtdiff._fmt_total(0.0, 0), "n/a")
        # a genuine 0.0 total with at least one valid value is retained
        self.assertEqual(mcrtdiff._fmt_total(0.0, 1), "0.00")
        self.assertEqual(mcrtdiff._fmt_total(12.5, 3), "12.50")

    def test_all_instruments_union(self):
        builds = self._builds()
        self.assertEqual(mcrtdiff.all_instruments(builds), ["instrA", "instrB", "instrC"])

    def test_reference_oldest_default(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        self.assertEqual(ref["label"], "buildA")

    def test_reference_explicit(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, "buildB", self.warns)
        self.assertEqual(ref["label"], "buildB")

    def test_reference_missing_falls_back(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, "nope", self.warns)
        self.assertEqual(ref["label"], "buildA")
        self.assertTrue(any("nope" in w for w in self.warns))


class TestComparison(_Fixtures):
    def test_deltas(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        b = [b for b in builds if b["label"] == "buildB"][0]
        # instrA: 15 vs 10 -> +5.00 s, +50.0%
        self.assertEqual(mcrtdiff._delta_str(15.0, 10.0), "+5.00 s")
        self.assertEqual(mcrtdiff._rel_str(15.0, 10.0), "+50.0%")
        # instrB: reference missing -> n/a
        self.assertEqual(mcrtdiff._delta_str(5.0, None), "n/a")
        self.assertEqual(mcrtdiff._rel_str(5.0, None), "n/a")
        # class: instrA is slower than ref
        self.assertEqual(mcrtdiff._delta_class(15.0, 10.0), "bad")
        self.assertEqual(mcrtdiff._delta_class(5.0, None), "na")

    def test_sorted_by_change(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        keys, refvals, nonref = mcrtdiff._sorted_keys(builds, ref, "runtime")
        # instrA has the only comparable delta (5.0); instrB/instrC ref is None
        self.assertEqual(keys[0], "instrA")
        self.assertEqual(refvals["instrA"], 10.0)
        self.assertEqual(len(nonref), 1)

    def test_total_deltas(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        b = [b for b in builds if b["label"] == "buildB"][0]
        rt = mcrtdiff.build_total_value(ref, "runtime")  # 10.0
        bt = mcrtdiff.build_total_value(b, "runtime")   # 28.0
        # 28 vs 10 -> +18.00 s, +180.0%, slower
        self.assertEqual(mcrtdiff._delta_str(bt, rt), "+18.00 s")
        self.assertEqual(mcrtdiff._rel_str(bt, rt), "+180.0%")
        self.assertEqual(mcrtdiff._delta_class(bt, rt), "bad")
        # reference self-delta is zero (still "same")
        self.assertEqual(mcrtdiff._delta_str(rt, rt), "+0.00 s")
        self.assertEqual(mcrtdiff._rel_str(rt, rt), "+0.0%")
        self.assertEqual(mcrtdiff._delta_class(rt, rt), "same")

    def test_total_delta_na_when_no_ref_data(self):
        ref = {"label": "r", "path": "", "meta": {}, "tests": {"k": {"runtime": None}}, "mtime": 1.0}
        b = {"label": "x", "path": "", "meta": {}, "tests": {"k": {"runtime": 5.0}}, "mtime": 2.0}
        self.assertIsNone(mcrtdiff.build_total_value(ref, "runtime"))
        # no reference total -> comparison data unavailable -> n/a
        self.assertEqual(
            mcrtdiff._delta_str(mcrtdiff.build_total_value(b, "runtime"),
                                mcrtdiff.build_total_value(ref, "runtime")),
            "n/a",
        )
        self.assertEqual(mcrtdiff._rel_str(5.0, None), "n/a")


class TestRendering(_Fixtures):
    def test_html_contains_expected(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        html = mcrtdiff.render_html(builds, ref, "runtime", "T", self.warns)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("instrA", html)
        self.assertIn("+5.00 s", html)
        self.assertIn("n/a", html)
        self.assertIn("buildA", html)
        self.assertIn("buildB", html)
        # self-closing / balanced sanity: every <table has a </table>
        self.assertEqual(html.count("<table"), html.count("</table>"))

    def test_html_escapes_specials(self):
        # an instrument name with HTML metacharacters must be escaped
        d = os.path.join(self.root, "silly")
        os.makedirs(d)
        obj = {"a<b&c": _testobj(1.0)}
        obj["a<b&c"]["displayname"] = "a<b&c"
        obj["_meta"] = _meta("h", "c", "d")
        with open(os.path.join(d, "testresults_silly.json"), "w") as f:
            json.dump(obj, f)
        builds = self._builds([d])
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        html = mcrtdiff.render_html(builds, ref, "runtime", "T", self.warns)
        self.assertIn("a&lt;b&amp;c", html)
        self.assertNotIn("a<b&c", html)

    def test_text_summary(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        text = mcrtdiff.render_text(builds, ref, "runtime", None)
        self.assertIn("reference build: buildA", text)
        self.assertIn("total[runtime](s)", text)
        self.assertIn("+5.00 s", text)

    def test_text_totals_have_deltas(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        text = mcrtdiff.render_text(builds, ref, "runtime", None)
        # header columns for the total delta vs reference
        self.assertIn("d[s]", text)
        self.assertIn("d[%]", text)
        # buildB total 28 vs ref 10 -> +18.00 s / +180.0%
        self.assertIn("+18.00 s", text)
        self.assertIn("+180.0%", text)
        # reference row self-delta
        self.assertIn("+0.00 s", text)
        self.assertIn("+0.0%", text)

    def test_html_totals_have_deltas(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        html = mcrtdiff.render_html(builds, ref, "runtime", "T", self.warns)
        # total table header has the delta columns right after valid/total
        self.assertIn(
            "<th>total runtime [s]</th><th>valid/total</th>"
            "<th>&Delta; [s]</th><th>&Delta; [%]</th>",
            html,
        )
        # buildB total delta vs reference (28 vs 10)
        self.assertIn("+18.00 s", html)
        self.assertIn("+180.0%", html)
        # reference row is clearly marked
        self.assertIn('<tr class="ref">', html)
        self.assertIn("(ref)", html)

    def test_bar_same_style_and_legend(self):
        builds = self._builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        html = mcrtdiff.render_html(builds, ref, "runtime", "T", self.warns)
        # the reference bar (equal to itself) uses the visible "same" class
        self.assertIn('class="bar same"', html)
        # a visible background is defined for same bars and the legend swatch
        self.assertIn(".bar.same{background:", html)
        self.assertIn(".sw.same{background:", html)
        self.assertIn("same as ref", html)

    @staticmethod
    def _total_builds():
        # reference has data; "nov" has zero valid values; "zero" has a genuine 0.0
        b_ref = {"label": "ref", "path": "", "meta": {}, "tests": {"x": {"runtime": 10.0}}, "mtime": 1.0}
        b_nov = {"label": "nov", "path": "", "meta": {}, "tests": {"x": {"runtime": None}}, "mtime": 2.0}
        b_zero = {"label": "zero", "path": "", "meta": {}, "tests": {"x": {"runtime": 0.0}}, "mtime": 3.0}
        return [b_ref, b_nov, b_zero]

    def test_text_total_na_when_no_valid(self):
        builds = self._total_builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        text = mcrtdiff.render_text(builds, ref, "runtime", None)
        by_label = {ln.split(None, 1)[0]: ln for ln in text.splitlines() if ln.strip()}
        # no-valid build: total cell is n/a, never a spurious 0.00
        self.assertIn("n/a", by_label["nov"])
        self.assertNotIn("0.00", by_label["nov"])
        # genuine-zero build: total retained as 0.00
        self.assertIn("0.00", by_label["zero"])

    def test_html_total_na_when_no_valid(self):
        builds = self._total_builds()
        ref = mcrtdiff.pick_reference(builds, None, self.warns)
        html = mcrtdiff.render_html(builds, ref, "runtime", "T", self.warns)
        # total cell is a plain <td> (delta cells carry a class); the no-valid
        # build's total renders n/a and the genuine-zero build's renders 0.00
        self.assertIn("<td>n/a</td>", html)
        self.assertIn("<td>0.00</td>", html)


class TestEndToEnd(_Fixtures):
    def test_main_writes_report(self):
        out = os.path.join(self.root, "report.html")
        rc = mcrtdiff.main(
            [
                self.root,
                "--reflabel",
                "buildA",
                "--metric",
                "runtime",
                "--output",
                out,
                "--nobrowse",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(out))
        with open(out) as f:
            content = f.read()
        self.assertIn("<!DOCTYPE html>", content)
        self.assertIn("instrA", content)

    def test_main_compiletime_metric(self):
        out = os.path.join(self.root, "report_ct.html")
        rc = mcrtdiff.main(
            [self.root, "--metric", "compiletime", "--output", out, "--nobrowse"]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(out))

    def test_main_no_builds_fails(self):
        empty = tempfile.mkdtemp(prefix="mcrtdiff_empty_")
        try:
            rc = mcrtdiff.main([empty, "--nobrowse"])
            self.assertEqual(rc, 1)
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_main_single_build_warns_but_succeeds(self):
        pa = os.path.join(self.root, "buildA", "testresults_buildA.json")
        out = os.path.join(self.root, "report_one.html")
        rc = mcrtdiff.main([pa, "--output", out, "--nobrowse"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
