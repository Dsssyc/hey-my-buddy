"""Command-scope Git configuration for private test repositories and children."""
import os

# Local repository config cannot re-enable background writers during a copy.
TEST_GIT_ENV = {
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "maintenance.auto",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "gc.auto",
    "GIT_CONFIG_VALUE_1": "0",
}
os.environ.update(TEST_GIT_ENV)
