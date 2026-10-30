"""Decision logic of the password-failure limiter (pure, so it can be unit-tested without a database).

Counters (fixed windows, all counted BEFORE a password is verified and given back when the check succeeds or cannot be completed):
  pair   e-mail + IP      default limit  5   one address cannot guess against an account more than 5 times per window
  ip     IP, all e-mails  default limit 20   one address cannot spray many accounts
  email  e-mail, all IPs  default limit 30   brute force spread over many addresses is still capped (a lock-out then needs 30 failures
                                              from at least 6 different addresses, instead of 5 from one)
  global everything       default limit 2000 when exceeded the system is in ABUSE MODE: the per-IP limit drops to `abuse_ip_limit` (2),
                                              so only addresses that already failed are restricted; clean addresses are unaffected
An attempt is refused when, after counting it, the pair, ip (or abuse-mode ip) or email counter is above its limit.
"""

SCOPES = ("pair", "ip", "email", "global")


def effective_ip_limit(hits, limits, abuse_ip_limit):
    return abuse_ip_limit if hits["global"] > limits["global"] else limits["ip"]


def blocked_scope(hits, limits, abuse_ip_limit):
    """Name of the scope that refuses the attempt, or None. `hits` already includes the attempt being evaluated."""
    if hits["pair"] > limits["pair"]:
        return "pair"
    if hits["ip"] > effective_ip_limit(hits, limits, abuse_ip_limit):
        return "abuse_ip" if hits["global"] > limits["global"] and hits["ip"] <= limits["ip"] else "ip"
    if hits["email"] > limits["email"]:
        return "email"
    return None
