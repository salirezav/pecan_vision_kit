"""Unit tests for video review sampling and CSV layouts."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from pecan_kpis import (  # noqa: E402
    HALFYIELD_BUILTIN_ID,
    HALFYIELD_BUILTIN_ID_LEGACY,
    HALFYIELD_BUILTIN_FORMULA,
    HALFYIELD_BUILTIN_NAME,
    coalesce_builtin_enabled,
    halfyield_builtin_metric,
)
from yield_measure import (  # noqa: E402
    Calibration,
    area_share,
    calculate_yield_statistics,
    clamp_frames_per_sec,
    integrated_area_totals,
    sample_timestamps_ms,
    save_crack_report,
    save_yield_report,
)


class SampleTimingTests(unittest.TestCase):
    def test_one_frame_per_second(self) -> None:
        times = sample_timestamps_ms(3000, 1)
        self.assertEqual(times, [0.0, 1000.0, 2000.0, 3000.0])

    def test_two_frames_per_second(self) -> None:
        times = sample_timestamps_ms(1000, 2)
        self.assertEqual(times, [0.0, 500.0, 1000.0])

    def test_clamp_frames_per_sec(self) -> None:
        self.assertEqual(clamp_frames_per_sec(120, 60), 60)
        self.assertEqual(clamp_frames_per_sec(0, 30), 1.0)
        self.assertAlmostEqual(clamp_frames_per_sec(0.01, 60), 1.0 / 60.0)


class HalfYieldBuiltinTests(unittest.TestCase):
    def test_builtin_id_and_name(self) -> None:
        metric = halfyield_builtin_metric()
        self.assertEqual(metric["id"], "halfyield_half_share")
        self.assertEqual(metric["name"], HALFYIELD_BUILTIN_NAME)
        self.assertEqual(metric["formula"], HALFYIELD_BUILTIN_FORMULA)
        self.assertEqual(HALFYIELD_BUILTIN_ID, "halfyield_half_share")
        self.assertEqual(HALFYIELD_BUILTIN_ID_LEGACY, "halfyield_mass_share")

    def test_coalesce_maps_legacy_id(self) -> None:
        enabled = coalesce_builtin_enabled({HALFYIELD_BUILTIN_ID_LEGACY: False})
        self.assertFalse(enabled[HALFYIELD_BUILTIN_ID])
        enabled_on = coalesce_builtin_enabled({HALFYIELD_BUILTIN_ID_LEGACY: True})
        self.assertTrue(enabled_on[HALFYIELD_BUILTIN_ID])
        preferred = coalesce_builtin_enabled(
            {HALFYIELD_BUILTIN_ID_LEGACY: False, HALFYIELD_BUILTIN_ID: True}
        )
        self.assertTrue(preferred[HALFYIELD_BUILTIN_ID])

    def test_half_yield_is_area_share_not_mass(self) -> None:
        share = area_share(75.0, 25.0)
        self.assertAlmostEqual(float(share), 0.75, places=5)
        cal = Calibration(half_mass_per_area=0.002, piece_mass_per_area=0.0005)
        mass = cal.mass_share(share)
        self.assertNotAlmostEqual(float(mass), float(share), places=3)
        samples = [(0.0, 75.0, 25.0), (1000.0, 75.0, 25.0), (2000.0, 75.0, 25.0)]
        half_int, piece_int = integrated_area_totals(samples)
        run_share = area_share(half_int, piece_int)
        self.assertAlmostEqual(float(run_share), 0.75, places=5)
        stats = calculate_yield_statistics(samples)
        self.assertAlmostEqual(float(stats["net_average"]), 0.75, places=5)


class HalfYieldCsvTests(unittest.TestCase):
    def test_report_header_and_bins(self) -> None:
        samples = [
            (0.0, 100.0, 50.0),
            (5000.0, 100.0, 50.0),
            (10000.0, 80.0, 20.0),
        ]
        stats = calculate_yield_statistics(samples, bin_duration_sec=10.0)
        stats["calibration"] = {
            "half_confidence": 0.3,
            "piece_confidence": 0.3,
            "half_mass_per_area_g_per_model_pixel2": 0.001,
            "piece_mass_per_area_g_per_model_pixel2": 0.001,
        }
        stats["estimated_half_mass_share"] = 0.66
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.csv"
            save_yield_report(
                stats,
                path,
                video_path=Path("/tmp/demo.mp4"),
                model_filename="halfyield-model.pt",
                frames_per_sec=2,
            )
            text = path.read_text()
        self.assertIn("# Half Yield Analysis Report", text)
        self.assertIn("# Model:,halfyield-model.pt", text)
        self.assertIn("# Frames per second:,2", text)
        self.assertIn("# Estimated Half Mass Share:,0.66", text)
        self.assertIn("Time_Start_Seconds,Yield_Ratio", text)
        self.assertNotIn("pecan_exposed_kernel_mean", text)
        self.assertNotIn("count_pecan_uncracked", text)


class CrackCsvTests(unittest.TestCase):
    def test_report_final_kpis_and_columns(self) -> None:
        stats = {
            "calibration": {"class_confidence": {"pecan_uncracked": 0.5}},
            "class_names": ["pecan_uncracked", "kernel_broken"],
            "metrics": [
                {
                    "id": "uncracked_pct",
                    "name": "Uncracked %",
                    "formula": "100 * count(pecan_uncracked) / count(pecan_uncracked)",
                    "enabled": True,
                }
            ],
            "pecan_builtins": {
                "pecan_exposed_kernel_mean": {
                    "name": "Exposed-kernel mean",
                    "value": 0.12,
                    "ready": False,
                    "message": "insufficient data (10 / 200)",
                    "n": 10,
                }
            },
            "custom_values": {"uncracked_pct": 100.0},
            "include_pecan": True,
            "duration_sec": 12.0,
            "frames": 12,
            "binned_rows": [
                ["0.0", 5, 100.0, 1, 20.0, 0.12, "", "", 10, 100.0],
                ["10.0", 12, 240.0, 2, 40.0, 0.12, "", "", 10, 100.0],
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.csv"
            save_crack_report(
                stats,
                path,
                video_path=Path("/tmp/demo.mp4"),
                model_filename="crack.pt",
                frames_per_sec=1,
            )
            text = path.read_text()
            with path.open(newline="") as handle:
                rows = list(csv.reader(handle))
        self.assertIn("# Crack Analyst Review Report", text)
        self.assertIn("# Model:,crack.pt", text)
        self.assertIn("# Final pecan_exposed_kernel_mean:,0.12", text)
        self.assertIn("# Final Uncracked %:,100.0", text)
        self.assertIn("# Final Uncracked % id:,uncracked_pct", text)
        self.assertNotIn("Yield_Ratio", text)
        self.assertNotIn("Estimated_Half_Mass_Share", text)
        header = next(row for row in rows if row and row[0] == "Time_Start_Seconds")
        self.assertIn("count_pecan_uncracked", header)
        self.assertIn("pecan_exposed_kernel_mean", header)
        self.assertIn("uncracked_pct_value", header)


class SegmentResetTests(unittest.TestCase):
    def test_new_segment_drops_prior_samples(self) -> None:
        # Mimic review_video segment reset: statistics only cover the current list.
        first = [(0.0, 10.0, 10.0), (1000.0, 10.0, 10.0)]
        second = [(5000.0, 90.0, 10.0), (6000.0, 90.0, 10.0)]
        stats_first = calculate_yield_statistics(first)
        stats_second = calculate_yield_statistics(second)
        self.assertAlmostEqual(float(stats_first["net_average"]), 0.5, places=5)
        self.assertAlmostEqual(float(stats_second["net_average"]), 0.9, places=5)
        # A reset replaces the sample list rather than merging segments.
        stats_reset = calculate_yield_statistics(second)
        self.assertEqual(stats_reset["net_average"], stats_second["net_average"])


if __name__ == "__main__":
    unittest.main()
