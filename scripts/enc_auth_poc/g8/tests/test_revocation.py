"""Revocation timestamp edge cases: Firebase compares whole seconds (`iat < validSince`), so a token issued in the same second as the revoke is
ambiguous. An ambiguous or unavailable answer must never be a pass."""
import unittest

import _g8  # noqa: F401  (paths)

import g8_revocation as rev

W = rev.window(1000.4, 1000.9)         # -> (1000, 1000)
S = rev.SKEW_SECONDS


class Window(unittest.TestCase):
    def test_window_is_whole_seconds_and_refuses_nonsense(self):
        self.assertEqual(W, (1000, 1000))
        self.assertEqual(rev.window(1000.9, 1001.2), (1000, 1001))
        for bad in ((1001, 1000), (0, 5), (-1, 5), (True, 5), ("1", 5), (None, None)):
            self.assertIsNone(rev.window(*bad))


class OldToken(unittest.TestCase):
    def test_rejected_and_provably_older_is_revoked(self):
        self.assertEqual(rev.classify_old_token("rejected", 1000 - S - 1, W), rev.REVOKED)

    def test_rejected_but_not_provably_older_is_ambiguous_not_a_pass(self):
        for iat in (1000 - S, 1000, 1000 + S):
            self.assertEqual(rev.classify_old_token("rejected", iat, W), rev.AMBIGUOUS, iat)

    def test_accepted_and_provably_older_is_a_failure_of_revocation(self):
        self.assertEqual(rev.classify_old_token("accepted", 1000 - S - 1, W), rev.FAILED)

    def test_accepted_in_the_same_second_is_the_documented_edge_case_and_ambiguous(self):
        for iat in (999, 1000, 1001):
            self.assertEqual(rev.classify_old_token("accepted", iat, W), rev.AMBIGUOUS, iat)

    def test_a_token_issued_after_the_window_is_not_an_old_token(self):
        self.assertEqual(rev.classify_old_token("rejected", 1000 + S + 1, W), rev.FAILED)

    def test_errors_unknown_answers_and_missing_facts_are_ambiguous(self):
        self.assertEqual(rev.classify_old_token("error", 900, W), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_old_token(None, 900, W), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_old_token("rejected", None, W), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_old_token("rejected", True, W), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_old_token("rejected", 900, None), rev.AMBIGUOUS)


class NewToken(unittest.TestCase):
    def test_accepted_and_provably_newer_is_valid(self):
        self.assertEqual(rev.classify_new_token("accepted", 1000 + S + 1, W), rev.VALID)

    def test_accepted_inside_the_skew_band_is_ambiguous(self):
        for iat in (999, 1000, 1000 + S):
            self.assertEqual(rev.classify_new_token("accepted", iat, W), rev.AMBIGUOUS, iat)

    def test_rejected_and_provably_newer_is_a_failure(self):
        self.assertEqual(rev.classify_new_token("rejected", 1000 + S + 1, W), rev.FAILED)

    def test_rejected_inside_the_band_and_all_unknowns_are_ambiguous(self):
        self.assertEqual(rev.classify_new_token("rejected", 1000, W), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_new_token("error", 2000, W), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_new_token("accepted", None, W), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_new_token("accepted", 2000, None), rev.AMBIGUOUS)


class Refresh(unittest.TestCase):
    def test_token_expired_is_revoked(self):
        self.assertEqual(rev.classify_refresh("TOKEN_EXPIRED", W, 1010), rev.REVOKED)

    def test_a_successful_refresh_late_enough_is_a_failure_and_early_is_ambiguous(self):
        self.assertEqual(rev.classify_refresh("-", W, 1000 + S + 1), rev.FAILED)
        self.assertEqual(rev.classify_refresh(None, W, 1000 + S + 1), rev.FAILED)
        self.assertEqual(rev.classify_refresh("-", W, 1000), rev.AMBIGUOUS)

    def test_other_codes_and_missing_facts_are_ambiguous(self):
        self.assertEqual(rev.classify_refresh("USER_DISABLED", W, 2000), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_refresh("INVALID_REFRESH_TOKEN", W, 2000), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_refresh("-", None, 2000), rev.AMBIGUOUS)
        self.assertEqual(rev.classify_refresh("-", W, None), rev.AMBIGUOUS)


class Passing(unittest.TestCase):
    def test_only_revoked_and_valid_are_passes(self):
        self.assertTrue(rev.is_pass(rev.REVOKED))
        self.assertTrue(rev.is_pass(rev.VALID))
        self.assertFalse(rev.is_pass(rev.AMBIGUOUS))
        self.assertFalse(rev.is_pass(rev.FAILED))
        self.assertFalse(rev.is_pass(None))

    def test_the_settle_time_between_revoke_and_the_next_token_clears_the_skew_band(self):
        import g8_stages
        self.assertGreater(g8_stages.SETTLE_SECONDS, S + 1)


if __name__ == "__main__":
    unittest.main()
