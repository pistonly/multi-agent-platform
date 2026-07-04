"""Constants for topic advance-round participant acknowledgement."""

from datetime import timedelta

ADVANCE_ROUND_ACK_TIMEOUT = timedelta(hours=24)

ACK_ACCEPT_MARKER = "map:ack=accept"
ACK_REJECT_MARKER = "map:ack=reject"
ACK_DISMISS_MARKER = "map:ack=dismiss"
