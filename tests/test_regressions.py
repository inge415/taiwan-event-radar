import unittest

from taiwan_event_radar import radar


class CoverageRegressionTests(unittest.TestCase):
    def test_tickettw_style_maroon5_listing_is_parsed(self) -> None:
        html = """
        台灣演唱會2026｜門票資訊
        分享 主頁 【2026台灣演唱會】門票價錢・座位圖・搶票攻略｜官方購票連結
        台灣演唱會門票價錢、座位圖及官方購票連結 台灣演唱會2026｜門票資訊
        魔力紅 Maroon 5 高雄演唱會 2026 ｜門票價錢、座位圖、全面開賣時間、附官方購票連結
        演出日期: 2027年1月24日 (星期日)
        門票價錢: NT$ 9700 / 2500
        演出場館: 高雄國家體育場
        優先購票: 2026年8月11日12:00起 TIXCRAFT
        全面開賣: 2026年8月12日10:00起 TIXCRAFT
        """

        events = radar.parse_tickettw_events(html, "https://www.tickettw.com/")

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["name"], "魔力紅 Maroon 5 高雄演唱會 2026")
        self.assertIn("Maroon 5", event["name"])
        self.assertIn("魔力紅", event["name"])
        self.assertNotIn("主頁", event["name"])
        self.assertNotIn("官方購票連結", event["name"])
        self.assertEqual(event["sale_date"], "2026-08-11")
        self.assertEqual(event["sale_time"], "12:00")
        self.assertEqual(event["performance_date"], "2027-01-24")
        self.assertEqual(event["venue"], "高雄國家體育場")
        self.assertEqual(event["scope"], "large_music_taiwan")

    def test_performing_arts_search_item_parses_classical_general_sale(self) -> None:
        item = {
            "title": "世界級愛樂樂團2026台北音樂會售票資訊",
            "description": (
                "年度古典音樂盛事，知名指揮率團訪台登台。"
                "8/10 12:00 會員優先預購，8/12 12:00 全面啟售。"
                "演出日期：2026/11/20 臺北國家音樂廳"
            ),
            "link": "https://example.com/classical-concert",
        }

        event = radar.performing_arts_search_item_to_event(item, radar.dt.date(2026, 8, 9))

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["type"], "古典音樂")
        self.assertEqual(event["sale_date"], "2026-08-12")
        self.assertEqual(event["sale_time"], "12:00")
        self.assertEqual(event["performance_date"], "2026-11-20")
        self.assertEqual(event["city"], "台北市")
        self.assertEqual(event["venue"], "臺北國家音樂廳")
        self.assertEqual(event["scope"], "taipei_only")

    def test_english_classical_concert_is_not_large_pop_concert(self) -> None:
        event_type = radar.classify_event(
            "World Philharmonic Taipei Concert 2026",
            "",
            "臺北國家音樂廳",
        )

        self.assertEqual(event_type, "古典音樂")

    def test_classical_hall_concert_is_not_large_pop_concert(self) -> None:
        event_type = radar.classify_event(
            "International Soloist Live in Concert",
            "",
            "臺北國家音樂廳",
        )

        self.assertEqual(event_type, "古典音樂")

    def test_mna_listing_extracts_public_detail_urls(self) -> None:
        html = """
        <a href="/UTK0201_?PRODUCT_ID=P1ABC123">世界級愛樂樂團台北音樂會</a>
        <a href="UTK0201_?PRODUCT_ID=P2DEF456&kk=1">國際芭蕾舞團台北公演</a>
        """

        urls = radar.extract_mna_detail_urls(html, "https://ticket.mna.com.tw/UTK0102_?TYPE=0")

        self.assertIn("https://ticket.mna.com.tw/UTK0201_?PRODUCT_ID=P1ABC123", urls)
        self.assertIn("https://ticket.mna.com.tw/UTK0201_?PRODUCT_ID=P2DEF456&kk=1", urls)


if __name__ == "__main__":
    unittest.main()
