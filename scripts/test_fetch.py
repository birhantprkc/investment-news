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

    def test_items_without_timestamp_are_both_kept(self):
        """源没给时间时**不做**标题去重——这条原先写反了（codex 审计指出）。

        没有时间就无从判断这两条是同一新闻的转载，还是同名栏目的不同期。判错的代价
        不对等：多显示一条只是冗余，判错删掉是永久丢一篇。交给 URL 去重兜底。
        """
        items = [item("无日期新闻", "https://a.com/1", 0), item("无日期新闻", "https://b.com/2", 0)]
        self.assertEqual(len(dedup(items)), 2)

    def test_same_url_still_deduped_without_timestamp(self):
        """时间缺失也不影响 URL 去重——同一个链接仍然只留一条。"""
        items = [item("无日期新闻", "https://a.com/1", 0), item("无日期新闻", "https://a.com/1", 0)]
        self.assertEqual(len(dedup(items)), 1)

    def test_malformed_url_does_not_abort_the_run(self):
        """畸形链接不能让整次刷新崩掉（codex P1）。

        normalize_url 在 dedup() 里被调用，那已在 fetch() 的单源异常处理之外——
        urlsplit 抛 ValueError 会中断 main()，一条新闻都写不出来。
        """
        self.assertIsInstance(normalize_url("https://[broken"), str)
        items = [item("正常", "https://a.com/1"), item("畸形", "https://[broken")]
        self.assertEqual(len(dedup(items)), 2)

    def test_ambiguous_query_keys_are_not_stripped(self):
        """source/from/ref 这类通用词可能是文章标识，不能无条件剥（codex P2）。"""
        self.assertNotEqual(
            normalize_url("https://x.com/a?source=alpha"),
            normalize_url("https://x.com/a?source=beta"),
        )
        self.assertNotEqual(
            normalize_url("https://x.com/a?from=1"), normalize_url("https://x.com/a?from=2")
        )

    def test_unambiguous_tracking_params_still_stripped(self):
        """收紧之后，明确的跟踪参数仍要剥掉，否则去重就失效了。"""
        base = normalize_url("https://x.com/a")
        for q in ["utm_source=rss", "fbclid=abc", "gclid=xyz", "spm=1.2.3"]:
            self.assertEqual(normalize_url(f"https://x.com/a?{q}"), base, q)

    def test_transitive_duplicate_chain_collapses(self):
        """(A,url1) → (A,url2) → (改标题B,url2) 三条其实是同一条新闻。

        第二条被按标题丢掉时若不登记 url2，第三条就查不到 url2 已出现过，
        于是同一条新闻出两张卡（codex 复审指出）。
        """
        items = [
            item("英伟达发布新卡", "https://a.com/x", BASE_TS),
            item("英伟达发布新卡", "https://b.com/y", BASE_TS - 3600),
            item("NVIDIA 推出新一代加速卡", "https://b.com/y", BASE_TS - 7200),
        ]
        self.assertEqual(len(dedup(items)), 1)

    def test_skipped_item_does_not_over_dedup_later_distinct_news(self):
        """登记被丢弃条目的 key 不能反过来误删真正不同的新闻。"""
        items = [
            item("英伟达发布新卡", "https://a.com/x", BASE_TS),
            item("英伟达发布新卡", "https://b.com/y", BASE_TS - 3600),   # 转载，丢
            item("AMD 发布新卡", "https://c.com/z", BASE_TS - 7200),     # 不同新闻，留
        ]
        self.assertEqual(len(dedup(items)), 2)

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
