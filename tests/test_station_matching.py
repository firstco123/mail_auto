"""
最寄駅 and 沿線 scoring, which never actually worked.

The `train` taxonomy is three deep — 事業者 → 路線 → 駅 — and a listing carries
terms from every level in no fixed order. The client read the first of them as
the line and left the station empty, so across all 2,025 live listings
`nearest_station` was "" without exception. The scorer then compared "" with ""
and awarded its top tier, 100 points, to every candidate it scored; the 沿線
tier below it was written as "same line AND NOT same station" and so could
never fire at all.

The effect was not a missing bonus but a flat one: the two tiers meant to carry
the most weight in the ranking contributed nothing to the ordering.
"""
import unittest
from unittest.mock import MagicMock

from src.core.models import Property
from src.integrations.wp_client import WordPressClient
from src.matching.property_scorer import PropertyScorer

# 事業者 → 路線 → 駅, exactly as the live taxonomy is shaped.
_TERMS = {49: "名古屋鉄道", 37: "JR線",
          50: "名鉄名古屋本線", 39: "中央本線",
          4: "金山", 311: "千種", 99: "知立"}
_PARENTS = {49: 0, 37: 0,
            50: 49, 39: 37,
            4: 50, 311: 39, 99: 50}


def _client():
    wp = WordPressClient.__new__(WordPressClient)
    wp._fcfg = {"vacancy_status": "display_none", "vacancy_available_value": ""}
    wp._tcfg = {"train_line": "train", "category": []}
    wp._term_cache = {"train": dict(_TERMS)}
    wp._term_parent = {"train": dict(_PARENTS)}
    return wp


def _raw(wp_id, train_ids):
    return {"id": wp_id, "title": {"rendered": "物件"}, "link": "https://x/%d" % wp_id,
            "acf": {"display_none": "", "rent": 70000}, "train": train_ids}


class TestTheTaxonomyIsReadByDepth(unittest.TestCase):
    def test_a_station_is_no_longer_lost(self):
        p = _client()._to_property(_raw(1, [50, 4, 49]))
        self.assertEqual(p.nearest_station, "金山")
        self.assertEqual(p.train_line, "名鉄名古屋本線")

    def test_the_line_is_not_mistaken_for_a_station(self):
        """Order is arbitrary in the live data — depth must decide, not position."""
        p = _client()._to_property(_raw(1, [4, 49, 50]))   # station first
        self.assertEqual(p.nearest_station, "金山")
        self.assertEqual(p.train_line, "名鉄名古屋本線")

    def test_a_property_on_two_lines_keeps_both(self):
        p = _client()._to_property(_raw(1, [50, 4, 39, 311]))
        self.assertEqual(set(p.train_lines), {"名鉄名古屋本線", "中央本線"})
        self.assertEqual(set(p.stations), {"金山", "千種"})

    def test_an_operator_term_alone_yields_no_station(self):
        p = _client()._to_property(_raw(1, [49]))
        self.assertEqual(p.stations, [])
        self.assertEqual(p.nearest_station, "")

    def test_an_unreadable_hierarchy_does_not_crash(self):
        wp = _client()
        wp._term_parent = {}                      # parents never loaded
        p = wp._to_property(_raw(1, [50, 4]))
        self.assertEqual(p.stations, [])          # nothing claimed as a station


def _prop(wp_id, *, stations, lines, rent=70000, city="知立市"):
    return Property(
        wp_id=wp_id, name="P%d" % wp_id, url="u", rent=rent, management_fee=0,
        layout="1K", nearest_station=stations[0] if stations else "",
        train_line=lines[0] if lines else "", city=city, walk_minutes=5,
        category=[], equipment=[], is_vacant=True, is_commission_free=False,
        area_sqm=30.0, building_type="", stations=list(stations),
        train_lines=list(lines))


class TestScoringUsesRealStations(unittest.TestCase):
    def _score_of(self, inquiry, candidate):
        sc = PropertyScorer([inquiry, candidate])
        df = sc._df[(sc._df["wp_id"] == candidate.wp_id)]
        got = sc.find_alternatives(inquiry, top_n=1)
        self.assertEqual([p.wp_id for p in got], [candidate.wp_id])
        return df

    def test_an_unknown_station_no_longer_scores_as_a_match(self):
        """The whole defect in one assertion."""
        blank = _prop(1, stations=[], lines=[])
        other = _prop(2, stations=[], lines=[])
        sc = PropertyScorer([blank, other])
        a = sc.find_alternatives(blank, top_n=1)
        # It is still suggested — it is simply no longer credited with
        # sharing a station it has no record of.
        self.assertEqual([p.wp_id for p in a], [2])
        self.assertLess(self._raw_score(sc, blank, 2), 100)

    @staticmethod
    def _raw_score(sc, inquiry, wp_id):
        import pandas as pd
        df = sc._df[(sc._df["is_vacant"] == True) &
                    (sc._df["wp_id"] != inquiry.wp_id) &
                    (sc._df["rent"] > 0)].copy()
        want_st = frozenset(inquiry.stations or (
            [inquiry.nearest_station] if inquiry.nearest_station else []))
        want_ln = frozenset(inquiry.train_lines or (
            [inquiry.train_line] if inquiry.train_line else []))
        df["s"] = 0
        st = df["stations"].apply(lambda s: bool(s & want_st))
        df.loc[st, "s"] += 100
        ln = df["train_lines"].apply(lambda s: bool(s & want_ln))
        df.loc[ln & ~st, "s"] += 60
        return int(df[df["wp_id"] == wp_id]["s"].iloc[0])

    def test_a_shared_station_outranks_a_shared_line(self):
        inquiry = _prop(1, stations=["知立"], lines=["名鉄名古屋本線"])
        same_station = _prop(2, stations=["知立"], lines=["名鉄名古屋本線"])
        same_line = _prop(3, stations=["金山"], lines=["名鉄名古屋本線"])
        sc = PropertyScorer([inquiry, same_station, same_line])
        self.assertEqual([p.wp_id for p in sc.find_alternatives(inquiry, top_n=2)],
                         [2, 3])

    def test_the_line_tier_can_now_fire(self):
        inquiry = _prop(1, stations=["知立"], lines=["名鉄名古屋本線"])
        same_line = _prop(3, stations=["金山"], lines=["名鉄名古屋本線"])
        sc = PropertyScorer([inquiry, same_line])
        self.assertEqual(self._raw_score(sc, inquiry, 3), 60)

    def test_a_second_line_still_counts_as_a_match(self):
        inquiry = _prop(1, stations=["千種"], lines=["中央本線"])
        two_lines = _prop(2, stations=["金山"], lines=["名鉄名古屋本線", "中央本線"])
        sc = PropertyScorer([inquiry, two_lines])
        self.assertEqual(self._raw_score(sc, inquiry, 2), 60)

    def test_unrelated_rail_scores_neither_tier(self):
        inquiry = _prop(1, stations=["知立"], lines=["名鉄名古屋本線"])
        far = _prop(2, stations=["千種"], lines=["中央本線"])
        sc = PropertyScorer([inquiry, far])
        self.assertEqual(self._raw_score(sc, inquiry, 2), 0)

    def test_a_legacy_property_without_the_lists_still_matches(self):
        """Property objects rebuilt from older data carry only the scalars."""
        inquiry = Property(
            wp_id=1, name="a", url="u", rent=70000, management_fee=0, layout="1K",
            nearest_station="知立", train_line="名鉄名古屋本線", city="知立市",
            walk_minutes=5, category=[], equipment=[], is_vacant=True,
            is_commission_free=False, area_sqm=30.0, building_type="")
        cand = Property(
            wp_id=2, name="b", url="u", rent=70000, management_fee=0, layout="1K",
            nearest_station="知立", train_line="名鉄名古屋本線", city="知立市",
            walk_minutes=5, category=[], equipment=[], is_vacant=True,
            is_commission_free=False, area_sqm=30.0, building_type="")
        sc = PropertyScorer([inquiry, cand])
        self.assertEqual(self._raw_score(sc, inquiry, 2), 100)


if __name__ == "__main__":
    unittest.main()
