"""Histogram ranks preserve zero, fractional durations and an even median."""
from collections import Counter
import math
import unittest

from hey_my_buddy.blackboard.routing.statistics import approximate_median, bucket


class HistogramTests(unittest.TestCase):
    def test_logarithmic_bins_cover_zero_subsecond_and_large_values(self):
        self.assertEqual(bucket(0), (0, 0))
        self.assertIsNone(approximate_median({}))
        for value in (-1000, -1, -0.001, 0, 0.001, 0.1, 1, 12, 1000, 10**12):
            with self.subTest(value=value):
                actual = approximate_median({bucket(value): 1})
                if value == 0:
                    self.assertEqual(actual, 0)
                else:
                    self.assertLessEqual(abs(math.log(actual / value)), math.log(1.2) / 2 + 1e-12)

    def test_even_ranks_merge_counts_and_do_not_average_bucket_indexes(self):
        bins = Counter({bucket(0): 3, bucket(100): 2})
        self.assertEqual(approximate_median(bins), 0)
        bins.update({bucket(100): 1})
        self.assertAlmostEqual(approximate_median(bins), approximate_median({bucket(100): 1}) / 2)

    def test_signed_buckets_sort_by_value_including_negative_and_zero_medians(self):
        for values, expected in (((-100, -10, -1), -10), ((-100, -10, 0), -10), ((-10, 0, 1), 0)):
            with self.subTest(values=values):
                actual = approximate_median(Counter(bucket(value) for value in values))
                if expected:
                    self.assertLessEqual(abs(math.log(actual / expected)), math.log(1.2) / 2 + 1e-12)
                else:
                    self.assertEqual(actual, 0)
