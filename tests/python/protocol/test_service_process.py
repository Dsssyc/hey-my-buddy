"""Service startup leaves the caller's Job; service children stay invisible."""
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import service_process


class ServiceProcessTests(unittest.TestCase):
    def test_posix_service_starts_a_new_session(self):
        with mock.patch.object(service_process, "os", SimpleNamespace(name="posix")), \
             mock.patch.object(service_process.subprocess, "Popen") as popen:
            child = service_process.independent_service_popen(["python", "-m", "daemon"], close_fds=True)
        self.assertIs(child, popen.return_value)
        popen.assert_called_once_with(["python", "-m", "daemon"], close_fds=True, start_new_session=True)

    def test_windows_service_detaches_and_leaves_the_callers_job(self):
        with mock.patch.object(service_process, "os", SimpleNamespace(name="nt")), \
             mock.patch.object(service_process.subprocess, "Popen") as popen:
            service_process.independent_service_popen(["python", "-m", "daemon"], close_fds=True)
        popen.assert_called_once_with(["python", "-m", "daemon"], close_fds=True,
                                     creationflags=0x01000208)

    def test_windows_breakaway_denial_keeps_system_reason_and_never_retries(self):
        failure = OSError(5, "breakaway denied")
        failure.winerror = 5
        with mock.patch.object(service_process, "os", SimpleNamespace(name="nt")), \
             mock.patch.object(service_process.subprocess, "Popen", side_effect=failure) as popen:
            with self.assertRaises(BoardError) as caught:
                service_process.independent_service_popen(["python"])
        self.assertEqual(caught.exception.code, "SERVICE_START_UNSUPPORTED")
        self.assertIn("Independent service start is not supported in this environment", caught.exception.message)
        self.assertIn(str(failure), caught.exception.message)
        self.assertIs(caught.exception.__cause__, failure)
        popen.assert_called_once()

    def test_windows_missing_interpreter_propagates_the_original_exception(self):
        failure = FileNotFoundError(2, "interpreter not found")
        with mock.patch.object(service_process, "os", SimpleNamespace(name="nt")), \
             mock.patch.object(service_process.subprocess, "Popen", side_effect=failure) as popen:
            with self.assertRaises(FileNotFoundError) as caught:
                service_process.independent_service_popen(["missing-python"])
        self.assertIs(caught.exception, failure)
        popen.assert_called_once()

    def test_windows_other_errors_are_not_classified_by_errno(self):
        failures = [OSError(5, "errno 5 is not Windows access denied"), OSError("invalid process arguments")]
        failures[1].winerror = 87
        for failure in failures:
            with self.subTest(failure=failure), \
                 mock.patch.object(service_process, "os", SimpleNamespace(name="nt")), \
                 mock.patch.object(service_process.subprocess, "Popen", side_effect=failure) as popen:
                with self.assertRaises(OSError) as caught:
                    service_process.independent_service_popen(["python"])
                self.assertIs(caught.exception, failure)
                popen.assert_called_once()

    def test_posix_creation_failures_propagate_the_original_exception(self):
        denied = OSError(5, "simulated Windows access denied")
        denied.winerror = 5
        for failure in (FileNotFoundError(2, "interpreter not found"), PermissionError(13, "execution denied"), denied):
            with self.subTest(failure=failure), \
                 mock.patch.object(service_process, "os", SimpleNamespace(name="posix")), \
                 mock.patch.object(service_process.subprocess, "Popen", side_effect=failure) as popen:
                with self.assertRaises(OSError) as caught:
                    service_process.independent_service_popen(["python"])
                self.assertIs(caught.exception, failure)
                popen.assert_called_once()

    def test_service_console_child_flags_are_shared_by_platform(self):
        for platform, expected in (("nt", 0x08000000), ("posix", 0)):
            with self.subTest(platform=platform), \
                 mock.patch.object(service_process, "os", SimpleNamespace(name=platform)):
                self.assertEqual(service_process.service_creation_flags(), expected)


if __name__ == "__main__":
    unittest.main()
