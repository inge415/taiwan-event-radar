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

    def test_exhibition_without_sale_info_enters_state_as_event(self) -> None:
        item = {
            "title": "未來城市某特展10/1台北開展",
            "description": "展期10/1至12/31，於台北松山文創園區展出。",
            "link": "https://example.com/exhibition-announcement",
        }

        event = radar.public_search_item_to_event(item, radar.dt.date(2026, 8, 9))

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["type"], "展覽")
        self.assertEqual(event["sale_date"], "")
        self.assertEqual(event["performance_date"], "2026-10-01 ~ 2026-12-31")
        self.assertEqual(event["city"], "台北市")
        self.assertEqual(event["venue"], "松山文創園區")

    def test_exhibition_later_sale_info_becomes_new_sale_info_not_duplicate(self) -> None:
        base = {
            "title": "未來城市某特展10/1台北開展",
            "description": "展期10/1至12/31，於台北松山文創園區展出。",
            "link": "https://example.com/exhibition-announcement",
        }
        later = {
            **base,
            "description": "展期10/1至12/31，於台北松山文創園區展出。9/1 12:00早鳥售票。",
        }
        previous_event = radar.public_search_item_to_event(base, radar.dt.date(2026, 8, 9))
        current_event = radar.public_search_item_to_event(later, radar.dt.date(2026, 8, 10))

        assert previous_event is not None
        assert current_event is not None
        self.assertEqual(previous_event["id"], current_event["id"])
        new_events, new_sale_info, changed_events = radar.diff_events(
            {previous_event["id"]: previous_event},
            {current_event["id"]: current_event},
        )

        self.assertEqual(new_events, [])
        self.assertEqual(len(new_sale_info), 1)
        self.assertEqual(changed_events, [])
        self.assertEqual(new_sale_info[0]["sale_date"], "2026-09-01")
        self.assertEqual(new_sale_info[0]["sale_time"], "12:00")

    def test_exhibition_article_with_lifecycle_date_does_not_need_action_keyword(self) -> None:
        text = "沉浸式某展覽即將登場台北，展期10月1日至12月31日，地點為臺北市立美術館。"

        self.assertTrue(radar.public_item_is_relevant(text))

    def test_exhibition_roundup_noise_is_rejected(self) -> None:
        text = "台北熱門展覽總整理與旅遊攻略，持續更新各大票券列表。"

        self.assertFalse(radar.public_item_is_relevant(text))

    def test_invalid_flexible_dates_are_ignored(self) -> None:
        sale_date, sale_time = radar.parse_sale_from_text("30/04 19:00售票，正式日期待公告", 2026)

        self.assertEqual((sale_date, sale_time), ("", ""))

    def test_indexed_exhibition_listing_snippet_can_discover_multiple_events(self) -> None:
        item = {
            "title": "活動展演",
            "description": (
                "從前從前－繪說童話郵票特展2026-07-11～2027-01-03 "
                "100 臺北市中正區忠孝西路1段114號2樓(郵政博物館臺北館特展室) "
                "鐵道文化常設展2026-02-26～2026-12-31 "
                "100 臺北市中正區襄陽路2號(國立臺灣博物館)"
            ),
            "link": "https://travel.taipei/zh-tw/activity?category=exhibition&mode=list&page=1",
        }

        events = radar.exhibition_events_from_search_item(item, radar.dt.date(2026, 8, 9))

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "展覽")
        self.assertEqual(events[0]["sale_date"], "")
        self.assertEqual(events[0]["city"], "台北市")
        self.assertEqual(events[0]["performance_date"], "2026-07-11 ~ 2027-01-03")
        self.assertEqual(events[1]["performance_date"], "2026-02-26 ~ 2026-12-31")

    def test_venue_schedule_discovers_exhibition_without_sale_info(self) -> None:
        text = (
            "展會活動 1館 2026 10/01(四)-10/03(六) "
            "某台北國際藝術博覽會 地點：四樓展場 "
            "1館 2026 10/14(三)-10/16(五) 某台北攝影器材展 地點：一樓展場"
        )

        events = radar.parse_tainex_exhibition_schedule(text, "https://www.tainex.com.tw/event", 2026)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["sale_date"], "")
        self.assertEqual(events[0]["type"], "展覽")
        self.assertEqual(events[0]["performance_date"], "2026-10-01 ~ 2026-10-03")
        self.assertEqual(events[0]["city"], "台北市")

    def test_venue_schedule_rejects_b2b_or_job_fair_noise(self) -> None:
        text = (
            "展會活動 1館 2026 10/01(四)-10/03(六) "
            "某國際半導體展 地點：四樓展場 "
            "1館 2026 10/14(三)-10/16(五) CAKE 2026 職涯博覽會 地點：一樓展場"
        )

        events = radar.parse_tainex_exhibition_schedule(text, "https://www.tainex.com.tw/event", 2026)

        self.assertEqual(events, [])

    def test_exhibition_relevance_keeps_culture_entertainment_semantics(self) -> None:
        kept = (
            "某國際當代藝術博覽會",
            "某美術館沉浸式主題特展",
            "某動畫IP原畫展",
            "某博物館文化特展",
            "某台北攝影展",
        )

        for name in kept:
            with self.subTest(name=name):
                self.assertTrue(radar.exhibition_schedule_name_is_relevant(name))

    def test_exhibition_relevance_rejects_trade_or_procurement_semantics(self) -> None:
        rejected = (
            "某台北國際家具名床大展暨居家生活用品展",
            "某國際食品暨設備展",
            "某台北國際旅展",
            "某台北國際精緻酒展",
            "某佛事用品工藝品展",
            "某採購博覽會",
        )

        for name in rejected:
            with self.subTest(name=name):
                self.assertFalse(radar.exhibition_schedule_name_is_relevant(name))


if __name__ == "__main__":
    unittest.main()
