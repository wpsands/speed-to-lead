import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import stl  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
LATER = T0 + timedelta(days=30)


def company(**form):
    return {"id": "acme", "name": "Acme", "domain": "acme.example", "submitted_at": stl.iso(T0),
            "form": {"fields": 5, "scheduler": "none", "phone_required": False, **form}}


def touch(minutes, channel="email", kind="human", pers=0, meeting="none", cid="acme"):
    return {"company": cid, "channel": channel, "at": stl.iso(T0 + timedelta(minutes=minutes)), "kind": kind,
            "personalization": pers, "meeting": meeting}


class Bands(unittest.TestCase):
    def test_speed_band_edges(self):
        self.assertEqual(stl.speed_points(5), 40)
        self.assertEqual(stl.speed_points(5.1), 32)
        self.assertEqual(stl.speed_points(60), 24)
        self.assertEqual(stl.speed_points(1440), 8)
        self.assertEqual(stl.speed_points(4320), 3)
        self.assertEqual(stl.speed_points(4321), 0)
        self.assertEqual(stl.speed_points(None), 0)

    def test_grade_cuts(self):
        self.assertEqual([stl.grade(x) for x in (85, 84, 70, 55, 40, 39)], ["A", "B", "B", "C", "D", "F"])

    def test_max_score_is_100(self):
        self.assertEqual(stl.speed_points(0) + stl.persistence_points(9) + stl.channel_points(3)
                         + stl.personalization_points(2) + stl.meeting_points("instant", "none"), 100)


class ScoreCompany(unittest.TestCase):
    def test_auto_reply_does_not_count_as_first_human_touch(self):
        r = stl.score_company(company(), [touch(1, kind="auto"), touch(30)], 7, LATER)
        self.assertEqual(r["minutes_to_first_human"], 30)
        self.assertEqual(r["minutes_to_first_auto"], 1)
        self.assertEqual(r["points"]["speed"], 24)

    def test_touches_outside_window_are_ignored(self):
        r = stl.score_company(company(), [touch(60 * 24 * 8)], 7, LATER)
        self.assertIsNone(r["minutes_to_first_human"])
        self.assertEqual(r["human_touches"], 0)
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["grade"], "F")

    def test_call_and_voicemail_count_as_one_phone_channel(self):
        r = stl.score_company(company(), [touch(10, "call"), touch(20, "voicemail"), touch(30, "email")], 7, LATER)
        self.assertEqual(r["channels"], ["email", "phone"])
        self.assertEqual(r["points"]["channels"], 7)

    def test_instant_scheduler_takes_full_meeting_points(self):
        r = stl.score_company(company(scheduler="instant"), [], 7, LATER)
        self.assertEqual(r["points"]["meeting"], 15)
        self.assertEqual(r["best_meeting"], "instant scheduler")

    def test_best_meeting_and_personalization_are_maximums(self):
        r = stl.score_company(company(), [touch(5, pers=1, meeting="questions"), touch(50, pers=2, meeting="link")], 7, LATER)
        self.assertEqual(r["best_personalization"], 2)
        self.assertEqual(r["best_meeting"], "link")

    def test_window_still_open(self):
        r = stl.score_company(company(), [touch(5)], 7, T0 + timedelta(days=2))
        self.assertEqual(r["status"], "window open")

    def test_not_submitted(self):
        c = company(); c["submitted_at"] = None
        self.assertEqual(stl.score_company(c, [], 7, LATER)["status"], "not submitted")


class Validation(unittest.TestCase):
    def test_rejects_naive_time(self):
        with self.assertRaises(ValueError):
            stl.parse_time("2026-11-02T14:00:00")

    def test_rejects_unknown_company_and_channel(self):
        with self.assertRaises(ValueError):
            stl.validate_touch(touch(1, cid="nope"), {"acme"})
        bad = touch(1); bad["channel"] = "fax"
        with self.assertRaises(ValueError):
            stl.validate_touch(bad, {"acme"})

    def test_normalize_number(self):
        self.assertEqual(stl.normalize_number("(415) 555-0100"), "+14155550100")
        self.assertEqual(stl.normalize_number("+44 20 7946 0000"), "+442079460000")


class Twilio(unittest.TestCase):
    def fake_fetch(self, url):
        if "/Calls.json" in url:
            return {"calls": [
                {"sid": "CA1", "from": "+14155550100", "start_time": "Mon, 02 Nov 2026 14:20:00 +0000", "duration": "34", "status": "completed"},
                {"sid": "CA0", "from": "+14155550100", "start_time": "Sun, 01 Nov 2026 09:00:00 +0000", "duration": "0", "status": "no-answer"},
            ], "next_page_uri": None}
        return {"messages": [
            {"sid": "SM1", "from": "+12125550199", "date_sent": "Mon, 02 Nov 2026 15:00:00 +0000", "body": "Hi Page, want to grab time?"},
        ], "next_page_uri": None}

    def test_events_filtered_by_since_and_typed(self):
        ev = stl.twilio_events("+15555550000", T0, self.fake_fetch, "ACx")
        self.assertEqual([e["sid"] for e in ev], ["CA1", "SM1"])

    def test_attribution_queue_and_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(stl.main(["init", d]), 0)
            stl.main(["add-company", d, "--name", "Acme", "--domain", "acme.example"])
            stl.main(["submit", d, "--company", "acme", "--at", stl.iso(T0)])
            run = stl.Run(root)
            stl._write_json(root / "numbers.json", {"+14155550100": "acme"})
            ev = stl.twilio_events("+15555550000", T0, self.fake_fetch, "ACx")
            self.assertEqual(stl.attribute_events(run, ev), (1, 1))
            self.assertEqual(stl.attribute_events(run, ev), (0, 0), "second pull must not duplicate")
            t = run.touches()[0]
            self.assertEqual((t["channel"], t["source"], t["ref"]), ("voicemail", "twilio", "CA1"))
            stl.main(["map-number", d, "--number", "212-555-0199", "--company", "acme"])
            self.assertEqual(len(run.touches()), 2)
            self.assertEqual(stl._read_jsonl(root / "unmatched.jsonl"), [])


class EndToEnd(unittest.TestCase):
    def test_cli_flow_and_import_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            stl.main(["init", d, "--window-days", "7"])
            stl.main(["add-company", d, "--name", "Acme Corp", "--domain", "acme.example"])
            stl.main(["submit", d, "--company", "acme-corp", "--at", "2026-11-02T09:00:00-05:00", "--fields", "6"])
            stl.main(["touch", d, "--company", "acme-corp", "--channel", "email", "--at", "2026-11-02T09:04:00-05:00",
                      "--kind", "human", "--personalization", "2", "--meeting", "times"])
            f = Path(d) / "inbox.json"
            f.write_text(json.dumps([{"company": "acme-corp", "channel": "email", "at": "2026-11-03T10:00:00Z",
                                      "kind": "human", "personalization": 1, "meeting": "link", "ref": "gmail-1"}]))
            stl.main(["import", d, str(f)])
            stl.main(["import", d, str(f)])
            run = stl.Run(Path(d))
            self.assertEqual(len(run.touches()), 2)
            s = stl.score_run(run, now=LATER)
            r = s["companies"][0]
            self.assertEqual(r["minutes_to_first_human"], 4)
            self.assertEqual(r["total"], 40 + 10 + 4 + 15 + 12)
            self.assertIn("Acme Corp", stl.render_report(s))

    def test_sample_is_deterministic_and_complete(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            stl.build_sample(Path(a)); stl.build_sample(Path(b))
            ra, rb = (Path(x, "report.md").read_text() for x in (a, b))
            self.assertEqual(ra, rb)
            s = json.loads(Path(a, "scores.json").read_text())
            self.assertEqual(s["benchmark"]["companies_scored"], len(stl.SAMPLE))
            self.assertNotIn("—", ra, "no em dashes in report copy")


FUNNEL = dict(monthly_requests=200, qualify_rate=0.20, opp_rate=0.50, pipeline_per_opp=40000)


class Impact(unittest.TestCase):
    def test_parse_rate_forms(self):
        self.assertEqual([stl.parse_rate(x) for x in ("0.2", "20", "20%", 0.2)], [0.2, 0.2, 0.2, 0.2])
        with self.assertRaises(SystemExit):
            stl.parse_rate("140")

    def test_gap_share_log_scale(self):
        self.assertEqual(stl.gap_share(30, 60), 0.0)
        self.assertEqual(stl.gap_share(60, 60), 0.0)
        self.assertEqual(stl.gap_share(72 * 60, 60), 1.0)
        self.assertAlmostEqual(stl.gap_share(24 * 60, 60), 1.0)
        self.assertAlmostEqual(stl.gap_share(120, 60), 0.2181, places=3)

    def test_72_hours_to_1_hour_worked_example(self):
        m = stl.impact_model(current_minutes=72 * 60, target_minutes=60, **FUNNEL)
        cons, base, aggr = m["scenarios"]
        self.assertEqual(m["baseline"]["pipeline_per_month"], 800000)
        self.assertAlmostEqual(cons["qualify_rate"], 0.25)
        self.assertAlmostEqual(cons["extra_opps_per_month"], 5)
        self.assertAlmostEqual(cons["extra_pipeline_per_year"], 2_400_000)
        self.assertAlmostEqual(base["extra_pipeline_per_year"], 4_800_000)
        self.assertAlmostEqual(aggr["extra_pipeline_per_year"], 9_600_000)

    def test_already_fast_means_no_gain(self):
        m = stl.impact_model(current_minutes=4, target_minutes=60, **FUNNEL)
        self.assertTrue(all(s["extra_pipeline_per_year"] == 0 for s in m["scenarios"]))

    def test_cap_holds(self):
        m = stl.impact_model(current_minutes=10000, target_minutes=5, monthly_requests=100, qualify_rate=0.6,
                             opp_rate=1, pipeline_per_opp=1000, cap=0.8)
        aggr = m["scenarios"][2]
        self.assertAlmostEqual(aggr["qualify_rate"], 0.8)
        self.assertTrue(aggr["capped"])
        self.assertFalse(m["scenarios"][0]["capped"])

    def test_cap_never_lowers_a_rate_already_above_it(self):
        m = stl.impact_model(current_minutes=10000, target_minutes=5, monthly_requests=100, qualify_rate=0.95,
                             opp_rate=1, pipeline_per_opp=1000, cap=0.9)
        self.assertTrue(all(s["extra_qualified_per_month"] == 0 for s in m["scenarios"]))

    def test_own_data_replaces_scenarios(self):
        m = stl.impact_model(current_minutes=4320, target_minutes=60, qualify_rate_at_target=0.32, win_rate=0.25, **FUNNEL)
        self.assertEqual(m["source"], "own data")
        self.assertEqual(len(m["scenarios"]), 1)
        s = m["scenarios"][0]
        self.assertAlmostEqual(s["extra_qualified_per_month"], 24)
        self.assertAlmostEqual(s["extra_pipeline_per_year"], 24 * 0.5 * 40000 * 12)
        self.assertAlmostEqual(s["extra_bookings_per_year"], s["extra_pipeline_per_year"] * 0.25)

    def test_cli_uses_measured_time_and_no_response_uses_window(self):
        with tempfile.TemporaryDirectory() as d:
            stl.main(["init", d])
            for name in ("Acme", "Quiet Co"):
                stl.main(["add-company", d, "--name", name, "--domain", f"{stl.slugify(name)}.example"])
                stl.main(["submit", d, "--company", stl.slugify(name), "--at", "2026-11-02T14:00:00Z"])
            stl.main(["touch", d, "--company", "acme", "--channel", "email", "--at", "2026-11-05T14:00:00Z",
                      "--kind", "human"])
            for cid in ("acme", "quiet-co"):
                stl.main(["impact", d, "--company", cid, "--monthly-requests", "200", "--qualify-rate", "20%",
                          "--opp-rate", "50", "--pipeline-per-opp", "40000"])
            s = stl.score_run(stl.Run(Path(d)), now=LATER)
            by = {r["id"]: r for r in s["companies"]}
            self.assertEqual(by["acme"]["impact"]["inputs"]["current_minutes"], 72 * 60)
            self.assertFalse(by["acme"]["impact"]["no_response"])
            self.assertEqual(by["quiet-co"]["impact"]["inputs"]["current_minutes"], 7 * 1440)
            self.assertTrue(by["quiet-co"]["impact"]["no_response"])
            report = stl.render_report(s)
            self.assertIn("## What faster response is worth", report)
            self.assertIn("$2.4M", report)
            self.assertIn("modeled as that long", report)

    def test_fmt_money(self):
        self.assertEqual([stl.fmt_money(x) for x in (950, 4500, 600000, 2400000)], ["$950", "$4.5K", "$600K", "$2.4M"])


if __name__ == "__main__":
    unittest.main()
