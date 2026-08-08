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


if __name__ == "__main__":
    unittest.main()
