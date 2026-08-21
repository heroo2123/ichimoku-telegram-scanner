import unittest

import scanner
from v3 import dashboard_v32


class BenchmarkDigestTests(unittest.TestCase):
    def candidate(self, symbol: str, score: int, status: str = "active") -> scanner.Candidate:
        return scanner.Candidate.from_dict({
            "id": f"Crypto Spot|{symbol}|1D|bullish|cloud_breakout|2026-08-20",
            "market": "Crypto Spot",
            "symbol": symbol,
            "name": symbol,
            "direction": "bullish",
            "signal_type": "cloud_breakout",
            "date": "2026-08-20",
            "close": 100.0,
            "score": score,
            "grade": scanner.grade_for_score(score),
            "weekly_alignment": "opposed" if symbol == "BTCUSDT" else "aligned",
            "reasons": ["test"],
            "warnings": [],
            "metrics": {},
            "lifecycle_status": status,
        })

    def test_extended_btc_is_pinned_without_score_boost(self):
        candidates = [self.candidate(f"ALT{index}USDT", 9 - (index % 2)) for index in range(10)]
        btc = self.candidate("BTCUSDT", 4, "extended")
        candidates.append(btc)

        plan = scanner.build_delivery_plan(candidates)
        digest_symbols = [candidate.symbol for candidate in plan["digest"]]

        self.assertEqual(len(plan["digest"]), scanner.config.MAX_DIGEST_SIGNALS)
        self.assertEqual(digest_symbols[0], "BTCUSDT")
        self.assertIn("BTCUSDT", digest_symbols)
        self.assertEqual(btc.score, 4)
        self.assertEqual(btc.grade, "C")
        self.assertEqual(btc.lifecycle_status, "extended")
        self.assertEqual(plan["priority_digest_count"], 1)
        self.assertNotIn("BTCUSDT", [candidate.symbol for candidate in plan["details"]])
        self.assertTrue(scanner.compact_candidate_line(btc).startswith("📌 "))

    def test_terminal_benchmark_is_not_pinned(self):
        candidates = [self.candidate(f"ALT{index}USDT", 8) for index in range(10)]
        candidates.append(self.candidate("BTCUSDT", 8, "invalidated"))
        plan = scanner.build_delivery_plan(candidates)
        self.assertNotIn("BTCUSDT", [candidate.symbol for candidate in plan["full_report"]])
        self.assertNotIn("BTCUSDT", [candidate.symbol for candidate in plan["digest"]])

    def test_dashboard_requests_full_signal_window(self):
        self.assertIn("/api/signals?limit=1000", dashboard_v32.base.DASHBOARD_HTML)
        self.assertNotIn("/api/signals?limit=100'", dashboard_v32.base.DASHBOARD_HTML)


if __name__ == "__main__":
    unittest.main()
