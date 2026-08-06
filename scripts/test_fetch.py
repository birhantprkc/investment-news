#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch.py 的条目层去重测试（issue #1）。纯标准库，直接跑：

    python3 scripts/test_fetch.py

重点不只是"重复的要去掉"，更是"不重复的绝不能被去掉" —— 去重做过头是静默丢
新闻，比重复显示更糟，所以下面两条反向用例才是关键：
  · query 里带文章 id 的不同文章（?p=123 / ?p=456）必须都留下
  · 跨周复用的同名栏目（每周综述）必须都留下
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch import dedup, normalize_title, normalize_url  # noqa: E402

DAY = 86400
BASE_TS = 1_770_000_000


def item(title, url, ts=BASE_TS, source="某源"):
    return {"title": title, "url": url, "ts": ts, "source": source, "summary": ""}


class TestNormalizeUrl(unittest.TestCase):
    def test_tracking_params_are_stripped(self):
        a = normalize_url("https://x.com/a?utm_source=rss&utm_medium=feed")
        b = normalize_url("https://x.com/a")
        self.assertEqual(a, b)

    def test_meaningful_query_is_kept(self):
        """?p=123 与 ?p=456 是两篇文章，不能因为都带 query 就当成同一个。"""
        a = normalize_url("https://x.com/?p=123")
        b = normalize_url("https://x.com/?p=456")
        self.assertNotEqual(a, b)

    def test_fragment_and_trailing_slash_ignored(self):
        self.assertEqual(
            normalize_url("https://x.com/a/#section"), normalize_url("https://x.com/a")
        )

    def test_scheme_and_host_case_insensitive(self):
        self.assertEqual(
            normalize_url("HTTPS://X.COM/Path"), normalize_url("https://x.com/Path")
        )

    def test_empty_url(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url(None), "")


class TestNormalizeTitle(unittest.TestCase):
    def test_punctuation_and_case_ignored(self):
        self.assertEqual(
            normalize_title("Apple's Q3: Revenue Up!"), normalize_title("apple s q3 revenue up")
        )

    def test_different_titles_stay_different(self):
        self.assertNotEqual(normalize_title("英伟达发布新卡"), normalize_title("英伟达财报"))


class TestDedup(unittest.TestCase):
    def test_same_url_kept_once_newest_wins(self):
        items = [
            item("标题 A", "https://x.com/a", BASE_TS),
            item("标题 A 旧抓取", "https://x.com/a?utm_source=rss", BASE_TS - 3600),
        ]
        out = dedup(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ts"], BASE_TS)  # 排序在前的（最新）留下

    def test_syndicated_same_title_different_sources_deduped(self):
        """不同源转载同一条新闻：URL 不同、标题相同、时间相近 → 只留一条。"""
        items = [
            item("英伟达发布新一代加速卡", "https://a.com/1", BASE_TS, "A 媒体"),
            item("英伟达发布新一代加速卡", "https://b.com/2", BASE_TS - 7200, "B 媒体"),
        ]
        self.assertEqual(len(dedup(items)), 1)

    def test_recurring_column_across_weeks_is_kept(self):
        """「每周综述」这类固定栏目名跨周复用，绝不能被当成重复清掉。"""
        items = [
            item("每周综述", "https://a.com/w3", BASE_TS),
            item("每周综述", "https://a.com/w2", BASE_TS - 7 * DAY),
            item("每周综述", "https://a.com/w1", BASE_TS - 14 * DAY),
        ]
        self.assertEqual(len(dedup(items)), 3)

    def test_different_articles_with_query_ids_all_kept(self):
        """靠 query 区分文章的源，两篇都要留下（PR #2 的整段砍 query 会误删）。"""
        items = [
            item("文章一", "https://x.com/?p=123", BASE_TS),
            item("文章二", "https://x.com/?p=456", BASE_TS - 60),
        ]
        self.assertEqual(len(dedup(items)), 2)

    def test_items_without_timestamp(self):
        """源没给时间（ts=0）时，同标题按重复处理。"""
        items = [item("无日期新闻", "https://a.com/1", 0), item("无日期新闻", "https://b.com/2", 0)]
        self.assertEqual(len(dedup(items)), 1)

    def test_order_is_preserved(self):
        items = [
            item("第一条", "https://a.com/1", BASE_TS),
            item("第二条", "https://a.com/2", BASE_TS - 100),
            item("第三条", "https://a.com/3", BASE_TS - 200),
        ]
        self.assertEqual([i["title"] for i in dedup(items)], ["第一条", "第二条", "第三条"])

    def test_no_duplicates_means_nothing_removed(self):
        items = [item("A", "https://a.com/1", BASE_TS), item("B", "https://a.com/2", BASE_TS - 10)]
        self.assertEqual(len(dedup(items)), 2)

    def test_empty_input(self):
        self.assertEqual(dedup([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
