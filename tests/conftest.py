import os
import pytest

# Enable test auth override so existing test suites pass with simulated headers.
# This flag CANNOT be enabled in normal production mode.
os.environ["GOVFIX_TEST_AUTH_OVERRIDE"] = "1"
os.environ["EMAIL_PROVIDER"] = "local_outbox"
